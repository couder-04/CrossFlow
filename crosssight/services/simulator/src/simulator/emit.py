"""PlateRead emission with detection noise, Kafka, and optional sinks."""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from anpr_common.grammar import normalize_plate
from anpr_common.schemas import Direction, PlateFormat, PlateRead, VehicleClass

if TYPE_CHECKING:
    from anpr_common.config import Settings

    from simulator.cameras import Camera
    from simulator.scenarios import InjectedEvent
    from simulator.trips import TraversalEvent

from simulator.frames import FramePublisher
from simulator.vehicles import Vehicle

logger = logging.getLogger(__name__)

DETECTION_PROB = 0.92
OCR_NOISE_PROB = 0.06
CONFUSION_SUB_PROB = 0.5

CONFUSION_MAP = {
    "0": "O",
    "O": "0",
    "1": "I",
    "I": "1",
    "2": "Z",
    "Z": "2",
    "5": "S",
    "S": "5",
    "8": "B",
    "B": "8",
    "6": "G",
    "G": "6",
}


def _apply_ocr_noise(plate: str, rng: random.Random) -> tuple[str, float]:
    chars = list(plate)
    if not chars:
        return plate, 0.9
    pos = rng.randrange(len(chars))
    if rng.random() < CONFUSION_SUB_PROB and chars[pos] in CONFUSION_MAP:
        chars[pos] = CONFUSION_MAP[chars[pos]]
        confidence = rng.uniform(0.55, 0.78)
    else:
        alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
        chars[pos] = rng.choice([c for c in alphabet if c != chars[pos]])
        confidence = rng.uniform(0.45, 0.7)
    return "".join(chars), confidence


def build_plate_read(
    vehicle: Vehicle,
    camera: Camera,
    ts: datetime,
    direction: str | None,
    speed_kmh: float,
    rng: random.Random,
    force: bool = False,
) -> PlateRead | None:
    if not force and rng.random() > DETECTION_PROB:
        return None

    plate_raw = vehicle.plate_raw
    confidence = rng.uniform(0.88, 0.98)
    if not force and rng.random() < OCR_NOISE_PROB:
        plate_raw, confidence = _apply_ocr_noise(vehicle.plate_norm, rng)

    result = normalize_plate(plate_raw)
    char_conf = [min(1.0, confidence + rng.uniform(-0.05, 0.05)) for _ in plate_raw]

    return PlateRead(
        event_id=uuid4(),
        camera_id=camera.id,
        ts=ts if ts.tzinfo else ts.replace(tzinfo=UTC),
        plate_raw=plate_raw,
        plate_norm=result.norm,
        plate_valid=result.valid,
        plate_format=PlateFormat(result.format),
        confidence=round(confidence, 3),
        char_conf=char_conf,
        alternates=[],
        lane=int(rng.randint(1, max(1, camera.lanes))),
        direction=Direction(direction) if direction else None,
        vehicle_class=vehicle.vehicle_class,
        color=vehicle.color,
        make=vehicle.make,
        speed_kmh=round(speed_kmh, 1),
        crop_key=None,
        source="simulator",
    )


def build_injected_read(
    injection: InjectedEvent,
    camera: Camera,
    vehicle: Vehicle | None,
    rng: random.Random,
) -> PlateRead:
    v = vehicle or Vehicle(
        vehicle_id="inj",
        plate_norm=injection.plate_norm,
        plate_raw=injection.plate_norm,
        plate_valid=True,
        plate_format="standard",
        vehicle_class=VehicleClass.car,
        color="white",
        make="Unknown",
        home_zone="",
        work_zone="",
    )
    direction = injection.direction or camera.allowed_direction
    read = build_plate_read(
        v,
        camera,
        injection.ts,
        direction,
        speed_kmh=35.0,
        rng=rng,
        force=injection.force_emit,
    )
    if read is None:
        read = build_plate_read(v, camera, injection.ts, direction, 35.0, rng, force=True)
    assert read is not None
    return read


class ReadEmitter:
    """Publish PlateRead events to Kafka, ClickHouse, file, or callback."""

    def __init__(
        self,
        settings: Settings,
        callback: Callable[[PlateRead], None] | None = None,
        output_file: Path | None = None,
        use_kafka: bool = True,
        use_clickhouse: bool = False,
    ) -> None:
        self.settings = settings
        self.callback = callback
        self.output_file = output_file
        self.use_kafka = use_kafka
        self.use_clickhouse = use_clickhouse
        self._producer: Any = None
        self._ch_client: Any = None
        self._file_handle: Any = None
        self._batch: list[PlateRead] = []
        self.emitted = 0
        self.skipped = 0
        self._frames = FramePublisher(settings)

        if output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = output_file.open("a", encoding="utf-8")

    def connect(self) -> None:
        if self.use_kafka:
            try:
                from kafka import KafkaProducer

                self._producer = KafkaProducer(
                    bootstrap_servers=self.settings.kafka_bootstrap,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    linger_ms=50,
                    batch_size=16384,
                )
                logger.info("Kafka producer connected to %s", self.settings.kafka_bootstrap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Kafka unavailable (%s); reads will use fallback sinks only", exc)
                self._producer = None

        if self.use_clickhouse:
            try:
                import clickhouse_connect

                self._ch_client = clickhouse_connect.get_client(
                    host=self.settings.clickhouse_host,
                    port=self.settings.clickhouse_port,
                    username=self.settings.clickhouse_user,
                    password=self.settings.clickhouse_password,
                    database=self.settings.clickhouse_db,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ClickHouse unavailable (%s)", exc)
                self._ch_client = None

    def close(self) -> None:
        self.flush()
        if self._producer:
            self._producer.flush()
            self._producer.close()
        if self._file_handle:
            self._file_handle.close()
        self._frames.close()

    def flush(self) -> None:
        if self._batch and self._ch_client:
            self._write_clickhouse_batch(self._batch)
            self._batch.clear()
        if self._producer:
            self._producer.flush()

    def emit(self, read: PlateRead, camera_name: str | None = None) -> None:
        payload = read.model_dump(mode="json")
        if self.callback:
            self.callback(read)
        if self._file_handle:
            self._file_handle.write(json.dumps(payload) + "\n")
        if self._producer:
            self._producer.send(
                self.settings.topic_reads,
                key=read.plate_norm,
                value=payload,
            )
        if self._ch_client:
            self._batch.append(read)
            if len(self._batch) >= 500:
                self._write_clickhouse_batch(self._batch)
                self._batch.clear()
        self._frames.publish(read, camera_name=camera_name)
        self.emitted += 1

    def _write_clickhouse_batch(self, reads: list[PlateRead]) -> None:

        rows = []
        for r in reads:
            # approximate lat/lng from camera not stored on read — ingest worker enriches from camera table
            h3_r8, h3_r7 = 0, 0
            rows.append(
                [
                    str(r.event_id),
                    r.camera_id,
                    r.ts,
                    r.plate_raw,
                    r.plate_norm,
                    int(r.plate_valid),
                    r.plate_format.value,
                    r.confidence,
                    r.char_conf,
                    r.alternates,
                    r.lane,
                    r.direction.value if r.direction else None,
                    r.vehicle_class.value,
                    r.color,
                    r.make,
                    r.speed_kmh,
                    r.crop_key,
                    r.source,
                    h3_r8,
                    h3_r7,
                ]
            )
        try:
            self._ch_client.insert(
                "anpr_reads",
                rows,
                column_names=[
                    "event_id",
                    "camera_id",
                    "ts",
                    "plate_raw",
                    "plate_norm",
                    "plate_valid",
                    "plate_format",
                    "confidence",
                    "char_conf",
                    "alternates",
                    "lane",
                    "direction",
                    "vehicle_class",
                    "color",
                    "make",
                    "speed_kmh",
                    "crop_key",
                    "source",
                    "h3_r8",
                    "h3_r7",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ClickHouse insert failed: %s", exc)


def process_traversal(
    event: TraversalEvent,
    emitter: ReadEmitter,
    rng: random.Random,
) -> None:
    read = build_plate_read(
        event.vehicle,
        event.camera,
        event.ts,
        event.direction,
        event.speed_kmh,
        rng,
    )
    if read:
        emitter.emit(read, camera_name=event.camera.name)
    else:
        emitter.skipped += 1
