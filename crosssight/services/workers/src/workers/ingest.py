"""Ingest worker: validate, dedup, enrich, persist reads."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import UTC, datetime
from typing import Any

import orjson
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.structs import OffsetAndMetadata
from anpr_common.config import Settings, get_settings
from anpr_common.geo import enrich_h3
from anpr_common.schemas import PlateRead
from pydantic import ValidationError

from workers.db import ClickHouseClient, create_pg_pool, create_redis, load_cameras
from workers.dedup import HybridDedup, InMemoryDedup, RedisDedup
from workers.health import HealthServer

logger = logging.getLogger(__name__)

BATCH_INTERVAL_SEC = 1.0
BATCH_MAX_BACKOFF_SEC = 30.0
BATCH_MAX_ROWS = 5000
CONVOY_ZSET_TTL_SEC = 1800
CONVOY_ZSET_PREFIX = "convoy:cam"


def _parse_read(payload: bytes) -> PlateRead | None:
    try:
        data = orjson.loads(payload)
        return PlateRead.model_validate(data)
    except (ValidationError, orjson.JSONDecodeError) as exc:
        logger.warning("Invalid PlateRead: %s", exc)
        return None


def _read_to_ch_row(read: PlateRead, h3_r8: int, h3_r7: int) -> dict[str, Any]:
    return {
        "event_id": str(read.event_id),
        "camera_id": read.camera_id,
        "ts": read.ts.replace(tzinfo=None) if read.ts.tzinfo else read.ts,
        "plate_raw": read.plate_raw,
        "plate_norm": read.plate_norm,
        "plate_valid": int(read.plate_valid),
        "plate_format": read.plate_format.value,
        "confidence": read.confidence,
        "char_conf": read.char_conf,
        "alternates": read.alternates,
        "lane": read.lane,
        "direction": read.direction.value if read.direction else None,
        "vehicle_class": read.vehicle_class.value,
        "color": read.color,
        "make": read.make,
        "speed_kmh": read.speed_kmh,
        "crop_key": read.crop_key,
        "source": read.source,
        "h3_r8": h3_r8,
        "h3_r7": h3_r7,
        "track_id": read.track_id,
        "bbox": list(read.bbox or []),
    }


class IngestWorker:
    def __init__(self, settings: Settings | None = None, health_port: int = 8081) -> None:
        self.settings = settings or get_settings()
        self.health_port = health_port
        self._consumer: AIOKafkaConsumer | None = None
        self._health = HealthServer("ingest", health_port)
        self._stop = asyncio.Event()
        self._buffer: list[dict[str, Any]] = []
        self._heatmap_buffer: dict[tuple[int, datetime], int] = {}
        # Next offset to commit per (topic, partition). Advanced only as reads are buffered.
        self._pending_offsets: dict[tuple[str, int], int] = {}
        # Serialises flushes: the batch loop and the consume loop must never flush concurrently.
        self._flush_lock = asyncio.Lock()
        self._cameras: dict[str, Any] = {}
        self._redis: Any | None = None
        self._ch: ClickHouseClient | None = None
        self._dedup: HybridDedup | None = None
        self._pg_pool = None

    async def _load_cameras(self) -> None:
        self._pg_pool = await create_pg_pool(self.settings)
        self._cameras = await load_cameras(self._pg_pool)
        logger.info("Loaded %d cameras for H3 enrichment", len(self._cameras))

    def _track_offset(self, topic: str, partition: int, offset: int) -> None:
        """Record the highest next-offset for a partition as its read is buffered."""
        key = (topic, partition)
        next_offset = offset + 1
        current = self._pending_offsets.get(key)
        if current is None or next_offset > current:
            self._pending_offsets[key] = next_offset

    def _merge_offsets(self, offsets: dict[tuple[str, int], int]) -> None:
        """Put uncommitted offsets back, keeping the highest per partition."""
        for key, next_offset in offsets.items():
            current = self._pending_offsets.get(key)
            if current is None or next_offset > current:
                self._pending_offsets[key] = next_offset

    async def _commit_offsets(self, offsets: dict[tuple[str, int], int] | None = None) -> None:
        """Commit ``offsets`` (a snapshot taken with the batch), or everything pending."""
        if offsets is None:
            offsets, self._pending_offsets = self._pending_offsets, {}
        if self._consumer is None or not offsets:
            return
        try:
            await self._consumer.commit(
                {
                    TopicPartition(topic, partition): OffsetAndMetadata(next_offset, "")
                    for (topic, partition), next_offset in offsets.items()
                }
            )
        except Exception:
            self._merge_offsets(offsets)
            raise

    async def _flush_buffer(self) -> None:
        async with self._flush_lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if self._ch is None:
            return
        reads_batch: list[dict[str, Any]] | None = None
        heatmap_snapshot: dict[tuple[int, datetime], int] | None = None
        # Snapshot the buffers and the offsets in one step (no await in between). Reads that the
        # consume loop buffers while the insert below is in flight belong to the next flush, and
        # so do their offsets: committing them now would lose those reads if we crashed.
        offsets_snapshot, self._pending_offsets = self._pending_offsets, {}
        if self._buffer:
            reads_batch = self._buffer
            self._buffer = []
        if self._heatmap_buffer:
            heatmap_snapshot = self._heatmap_buffer
            self._heatmap_buffer = {}
        try:
            if reads_batch:
                await self._ch.insert_reads_async(reads_batch)
                logger.debug("Inserted %d reads into ClickHouse", len(reads_batch))
            if heatmap_snapshot:
                rows = [
                    {
                        "h3_cell": h3_cell,
                        "minute": minute.replace(tzinfo=None) if minute.tzinfo else minute,
                        "count": count,
                    }
                    for (h3_cell, minute), count in heatmap_snapshot.items()
                ]
                await self._ch.insert_heatmap_1min_async(rows)
        except BaseException:
            if reads_batch:
                self._buffer = reads_batch + self._buffer
            if heatmap_snapshot:
                for key, count in heatmap_snapshot.items():
                    self._heatmap_buffer[key] = self._heatmap_buffer.get(key, 0) + count
            self._merge_offsets(offsets_snapshot)
            raise
        await self._commit_offsets(offsets_snapshot)

    async def _handle_read(self, read: PlateRead) -> None:
        assert self._dedup is not None
        ts_epoch = read.ts.timestamp()
        if await self._dedup.is_duplicate(read.plate_norm, read.camera_id, ts_epoch):
            return

        cam = self._cameras.get(read.camera_id)
        if cam is None:
            logger.warning("Unknown camera %s, skipping H3 enrich", read.camera_id)
            h3_r8, h3_r7 = 0, 0
        else:
            h3_r8, h3_r7 = enrich_h3(cam.lat, cam.lng, self.settings)

        self._buffer.append(_read_to_ch_row(read, h3_r8, h3_r7))

        minute = read.ts.replace(second=0, microsecond=0)
        if minute.tzinfo is None:
            minute = minute.replace(tzinfo=UTC)
        key = (h3_r8, minute)
        self._heatmap_buffer[key] = self._heatmap_buffer.get(key, 0) + 1

        assert self._redis is not None
        plate = read.plate_norm.upper()
        await self._redis.hset(
            f"lastseen:{plate}",
            mapping={
                "camera_id": read.camera_id,
                "ts": read.ts.replace(tzinfo=UTC).isoformat()
                if read.ts.tzinfo is None
                else read.ts.isoformat(),
                "confidence": str(read.confidence),
            },
        )
        await self._redis.publish(
            "reads",
            json.dumps(
                {
                    "camera_id": read.camera_id,
                    "plate_norm": plate,
                    "confidence": read.confidence,
                    "vehicle_class": read.vehicle_class.value,
                    "track_id": read.track_id,
                    "bbox": read.bbox,
                    "lane": read.lane,
                    "direction": read.direction.value if read.direction else None,
                    "crop_key": read.crop_key,
                    "ts": read.ts.isoformat(),
                }
            ),
        )
        await self._redis.publish(
            "heatmap",
            json.dumps(
                {
                    "h3_cell": h3_r8,
                    "h3": h3_r8,
                    "count": 1,
                    "minute": minute.isoformat(),
                }
            ),
        )
        zkey = f"{CONVOY_ZSET_PREFIX}:{read.camera_id}"
        await self._redis.zadd(zkey, {plate: ts_epoch})
        await self._redis.expire(zkey, CONVOY_ZSET_TTL_SEC)

    async def _batch_loop(self) -> None:
        backoff = BATCH_INTERVAL_SEC
        while not self._stop.is_set():
            await asyncio.sleep(backoff)
            if not (self._buffer or self._heatmap_buffer or self._pending_offsets):
                continue
            try:
                await self._flush_buffer()
            except Exception:
                # Keep the task alive: the data is back in the buffer and is retried next round.
                backoff = min(backoff * 2, BATCH_MAX_BACKOFF_SEC)
                logger.exception("Batch flush failed; retrying in %.0fs", backoff)
            else:
                backoff = BATCH_INTERVAL_SEC

    async def _consume_loop(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            if self._stop.is_set():
                break
            read = _parse_read(msg.value)
            if read is None:
                continue
            buffered_before = len(self._buffer)
            await self._handle_read(read)
            if len(self._buffer) > buffered_before:
                self._track_offset(msg.topic, msg.partition, msg.offset)
            if len(self._buffer) >= BATCH_MAX_ROWS:
                await self._flush_buffer()

    async def run(self) -> None:
        logging.basicConfig(level=logging.INFO)
        await self._health.start()
        self._redis = await create_redis(self.settings)
        self._ch = ClickHouseClient(self.settings)
        await self._ch.ensure_schema_async()
        memory = InMemoryDedup()
        redis_dedup = RedisDedup(self._redis)
        self._dedup = HybridDedup(memory, redis_dedup)
        await self._load_cameras()

        self._consumer = AIOKafkaConsumer(
            self.settings.topic_reads,
            bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="anpr-ingest",
            enable_auto_commit=False,
            auto_offset_reset="latest",
        )
        await self._consumer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        batch_task = asyncio.create_task(self._batch_loop())
        try:
            await self._consume_loop()
        finally:
            self._stop.set()
            batch_task.cancel()
            try:
                await batch_task
            except asyncio.CancelledError:
                pass
            try:
                await self._flush_buffer()
            except Exception:
                logger.exception("Shutdown flush failed; offsets left uncommitted")
            else:
                await self._commit_offsets()
            if self._consumer:
                await self._consumer.stop()
            if self._pg_pool:
                await self._pg_pool.close()
            if self._redis:
                await self._redis.aclose()
            await self._health.stop()
