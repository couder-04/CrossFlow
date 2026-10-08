"""Analytics worker: flow windows, segment speeds, OD, congestion, anomalies."""

from __future__ import annotations

import asyncio
import logging
import signal
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from anpr_common.config import Settings, get_settings
from anpr_common.geo import enrich_h3
from anpr_common.schemas import FlowWindow, PlateRead, VehicleClass
from pydantic import ValidationError

from workers.db import (
    CameraInfo,
    CameraPair,
    ClickHouseClient,
    create_pg_pool,
    create_redis,
    load_camera_pairs,
    load_cameras,
)
from workers.health import HealthServer

logger = logging.getLogger(__name__)

WINDOW_MINUTES = 5
SEGMENT_SLOW_FACTOR = 3.0
CONGESTION_THRESHOLD = 0.5
BOTTLENECK_FLOW_RATIO = 0.6
ANOMALY_Z_THRESHOLD = 3.0


def _floor_window(ts: datetime, minutes: int = WINDOW_MINUTES) -> datetime:
    ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
    minute = (ts.minute // minutes) * minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


def _hour_of_week(ts: datetime) -> int:
    ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
    return ts.weekday() * 24 + ts.hour


@dataclass
class PlateState:
    last_camera: str | None = None
    last_ts: datetime | None = None
    last_h3_r7: int | None = None
    trip_start_h3: int | None = None
    trip_start_ts: datetime | None = None
    trip_start_camera: str | None = None
    sightings: list[tuple[datetime, str, int]] = field(default_factory=list)


@dataclass
class CameraWindow:
    window_start: datetime
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    speeds: list[float] = field(default_factory=list)
    lane: int | None = None

    @property
    def volume(self) -> int:
        return sum(self.counts.values())

    def avg_speed(self) -> float | None:
        return statistics.mean(self.speeds) if self.speeds else None


class AnalyticsEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.windows: dict[tuple[str, int | None, datetime], CameraWindow] = {}
        self.plate_states: dict[str, PlateState] = {}
        self.pair_samples: dict[tuple[str, str, datetime], list[tuple[float, float]]] = defaultdict(
            list
        )
        self.pair_medians: dict[tuple[str, str], float] = {}
        self.adjacent_pairs: dict[tuple[str, str], float] = {}
        self.free_flow: dict[tuple[str, str], float] = {}
        self.volume_baselines: dict[tuple[str, int], tuple[float, float]] = {}
        self.segment_congestion: dict[tuple[str, str], float] = {}
        self.bottlenecks: list[dict[str, Any]] = []
        self.anomalies: list[dict[str, Any]] = []
        self.od_buffer: list[dict[str, Any]] = []
        self.od_camera_buffer: list[dict[str, Any]] = []
        self.dwell_states: dict[str, Any] = {}
        self.dwell_events: list[dict[str, Any]] = []
        self.travel_rows: list[dict[str, Any]] = []
        self.watermark: datetime | None = None

    def set_topology(
        self,
        pairs: list[CameraPair],
        free_flow: dict[tuple[str, str], float],
        baselines: dict[tuple[str, int], tuple[float, float]],
    ) -> None:
        self.adjacent_pairs = {(p.camera_a, p.camera_b): p.distance_m for p in pairs if p.adjacent}
        self.free_flow = free_flow
        self.volume_baselines = baselines

    def process_read(self, read: PlateRead, h3_r7: int) -> list[FlowWindow]:
        ts = read.ts.replace(tzinfo=None) if read.ts.tzinfo else read.ts
        if self.watermark is None or ts > self.watermark:
            self.watermark = ts
        ws = _floor_window(read.ts)
        key = (read.camera_id, read.lane, ws)
        if key not in self.windows:
            self.windows[key] = CameraWindow(window_start=ws, lane=read.lane)
        win = self.windows[key]
        win.counts[read.vehicle_class.value] += 1
        if read.speed_kmh is not None:
            win.speeds.append(read.speed_kmh)

        self._update_od(read, h3_r7)
        self._update_segment_speed(read)
        self._update_dwell(read)
        self._check_volume_anomaly(read.camera_id, read.ts, win.volume)

        return self._maybe_emit_flow(read.camera_id, read.lane, ws)

    def _update_segment_speed(self, read: PlateRead) -> None:
        state = self.plate_states.setdefault(read.plate_norm, PlateState())
        if state.last_camera and state.last_ts:
            pair = (state.last_camera, read.camera_id)
            dist = self.adjacent_pairs.get(pair)
            if dist is not None:
                travel_s = (read.ts - state.last_ts).total_seconds()
                if travel_s > 0:
                    speed_kmh = (dist / travel_s) * 3.6
                    max_speed = self.settings.max_urban_speed_kmh
                    median_tt = self.pair_medians.get(pair)
                    too_slow = median_tt is not None and travel_s > SEGMENT_SLOW_FACTOR * median_tt
                    if speed_kmh <= max_speed and not too_slow:
                        ws = _floor_window(read.ts)
                        self.pair_samples[(pair[0], pair[1], ws)].append((travel_s, speed_kmh))
        state.last_camera = read.camera_id
        state.last_ts = read.ts

    def _close_trip(self, state: PlateState, hour_ts: datetime) -> None:
        if (
            state.trip_start_h3 is not None
            and state.last_h3_r7 is not None
            and state.trip_start_h3 != state.last_h3_r7
        ):
            hour = datetime(hour_ts.year, hour_ts.month, hour_ts.day, hour_ts.hour, tzinfo=UTC)
            self.od_buffer.append(
                {
                    "origin_h3": state.trip_start_h3,
                    "dest_h3": state.last_h3_r7,
                    "hour": hour,
                    "trip_count": 1,
                }
            )
        if (
            state.trip_start_camera
            and state.last_camera
            and state.trip_start_camera != state.last_camera
        ):
            hour = datetime(hour_ts.year, hour_ts.month, hour_ts.day, hour_ts.hour, tzinfo=UTC)
            self.od_camera_buffer.append(
                {
                    "origin_camera": state.trip_start_camera,
                    "dest_camera": state.last_camera,
                    "hour": hour.replace(tzinfo=None),
                    "trip_count": 1,
                }
            )

    def _update_od(self, read: PlateRead, h3_r7: int) -> None:
        gap = timedelta(minutes=self.settings.trip_gap_min)
        state = self.plate_states.setdefault(read.plate_norm, PlateState())
        if state.trip_start_ts and state.last_ts and (read.ts - state.last_ts) > gap:
            self._close_trip(state, state.last_ts)
            state.trip_start_h3 = h3_r7
            state.trip_start_ts = read.ts
            state.trip_start_camera = read.camera_id
        elif state.trip_start_h3 is None:
            state.trip_start_h3 = h3_r7
            state.trip_start_ts = read.ts
            state.trip_start_camera = read.camera_id
        state.last_h3_r7 = h3_r7

    def _check_volume_anomaly(self, camera_id: str, ts: datetime, volume: int) -> None:
        how = _hour_of_week(ts)
        baseline = self.volume_baselines.get((camera_id, how))
        if not baseline:
            return
        mu, sigma = baseline
        if sigma <= 0:
            return
        z = (volume - mu) / sigma
        if z > ANOMALY_Z_THRESHOLD:
            self.anomalies.append(
                {
                    "camera_id": camera_id,
                    "ts": ts.isoformat(),
                    "volume": volume,
                    "z_score": z,
                    "baseline_mean": mu,
                }
            )

    def _watermark_floor(self) -> datetime:
        # Prefer event-time watermark so 60× simulation can close windows
        # even when sim timestamps run ahead of wall clock.
        if self.watermark is not None:
            return _floor_window(self.watermark)
        return _floor_window(datetime.now(UTC))

    def _maybe_emit_flow(self, camera_id: str, lane: int | None, ws: datetime) -> list[FlowWindow]:
        now_ws = self._watermark_floor()
        if ws >= now_ws:
            return []
        key = (camera_id, lane, ws)
        win = self.windows.pop(key, None)
        if win is None:
            return []
        return [self._flow_window(camera_id, win)]

    def _camera_free_flow(self, camera_id: str) -> float | None:
        speeds = [
            v
            for (a, b), v in self.free_flow.items()
            if (a == camera_id or b == camera_id) and v and v > 0
        ]
        if not speeds:
            return None
        return statistics.median(speeds)

    def _flow_window(self, camera_id: str, win: CameraWindow) -> FlowWindow:
        avg = win.avg_speed()
        congestion = None
        ff = self._camera_free_flow(camera_id)
        if avg is not None and ff and ff > 0:
            congestion = max(0.0, min(1.0, 1.0 - (avg / ff)))
        else:
            # Fall back to mean segment congestion touching this camera.
            segs = [
                ci
                for (a, b), ci in self.segment_congestion.items()
                if a == camera_id or b == camera_id
            ]
            if segs:
                congestion = max(0.0, min(1.0, statistics.mean(segs)))
        return FlowWindow(
            camera_id=camera_id,
            window_start=win.window_start,
            window_end=win.window_start + timedelta(minutes=WINDOW_MINUTES),
            counts_by_class=dict(win.counts),
            avg_speed_kmh=avg,
            congestion_index=congestion,
            volume=win.volume,
            lane=win.lane,
        )

    def flush_closed_windows(self) -> list[FlowWindow]:
        now_ws = self._watermark_floor()
        out: list[FlowWindow] = []
        for key in list(self.windows):
            cam, _lane, ws = key
            if ws < now_ws:
                win = self.windows.pop(key)
                out.append(self._flow_window(cam, win))
        return out

    def close_idle_trips(self) -> None:
        """Close OD trips idle longer than TRIP_GAP_MIN relative to watermark."""
        if self.watermark is None:
            return
        gap = timedelta(minutes=self.settings.trip_gap_min)
        for state in self.plate_states.values():
            if state.last_ts is None:
                continue
            last = state.last_ts.replace(tzinfo=None) if state.last_ts.tzinfo else state.last_ts
            if (self.watermark - last) > gap:
                self._close_trip(state, last)
                state.trip_start_h3 = None
                state.trip_start_ts = None
                state.trip_start_camera = None

    def aggregate_segments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (a, b, ws), samples in list(self.pair_samples.items()):
            if not samples:
                continue
            travel_times = [s[0] for s in samples]
            speeds = [s[1] for s in samples]
            med_tt = statistics.median(travel_times)
            med_spd = statistics.median(speeds)
            self.pair_medians[(a, b)] = med_tt
            ordered = sorted(travel_times)
            p90_index = min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))
            self.travel_rows.append(
                {
                    "camera_a": a,
                    "camera_b": b,
                    "window_start": ws,
                    "sample_count": len(samples),
                    "avg_travel_s": float(statistics.fmean(travel_times)),
                    "median_travel_s": float(med_tt),
                    "min_travel_s": float(min(travel_times)),
                    "max_travel_s": float(max(travel_times)),
                    "p90_travel_s": float(ordered[p90_index]),
                    "avg_speed_kmh": float(statistics.fmean(speeds)),
                    "distance_m": float(self.adjacent_pairs.get((a, b), 0.0)),
                }
            )
            rows.append(
                {
                    "camera_a": a,
                    "camera_b": b,
                    "window_start": ws,
                    "median_travel_s": float(med_tt),
                    "median_speed_kmh": float(med_spd),
                    "sample_count": len(samples),
                }
            )
            ff = self.free_flow.get((a, b), med_spd)
            if ff and ff > 0:
                ci = 1.0 - (med_spd / ff)
                self.segment_congestion[(a, b)] = max(0.0, min(1.0, ci))
        self.pair_samples.clear()
        return rows

    def detect_bottlenecks(
        self, flow_by_camera: dict[str, int], how: int | None = None
    ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        how_key = how if how is not None else 0
        for (a, b), ci in self.segment_congestion.items():
            if ci < CONGESTION_THRESHOLD:
                continue
            baseline = self.volume_baselines.get((b, how_key))
            if not baseline and how_key != 0:
                # Fall back to any known hour baseline for camera b.
                baseline = next(
                    (v for (cam, _), v in self.volume_baselines.items() if cam == b),
                    None,
                )
            if not baseline:
                continue
            mu, _ = baseline
            current = flow_by_camera.get(b, 0)
            if mu > 0 and current < BOTTLENECK_FLOW_RATIO * mu:
                found.append(
                    {
                        "segment": [a, b],
                        "congestion_index": ci,
                        "downstream_camera": b,
                        "flow": current,
                        "baseline": mu,
                    }
                )
        self.bottlenecks = found
        return found

    def drain_od(self) -> list[dict[str, Any]]:
        rows = self.od_buffer
        self.od_buffer = []
        return rows

    def drain_od_camera(self) -> list[dict[str, Any]]:
        rows = self.od_camera_buffer
        self.od_camera_buffer = []
        return rows

    def drain_dwell(self) -> list[dict[str, Any]]:
        rows = self.dwell_events
        self.dwell_events = []
        return rows

    def drain_travel(self) -> list[dict[str, Any]]:
        rows = self.travel_rows
        self.travel_rows = []
        return rows

    def _update_dwell(self, read: PlateRead) -> None:
        from anpr_common.intelligence.dwell import DwellObservation, DwellThresholds, advance_dwell

        thresholds = DwellThresholds(
            short_s=float(getattr(self.settings, "stopped_short_s", 45)),
            excessive_s=float(getattr(self.settings, "stopped_excessive_s", 120)),
            incident_s=float(getattr(self.settings, "stopped_incident_s", 180)),
            gap_s=float(getattr(self.settings, "stopped_gap_s", 90)),
        )
        ts = read.ts if read.ts.tzinfo else read.ts.replace(tzinfo=UTC)
        obs = DwellObservation(
            vehicle_id=read.plate_norm,
            plate=read.plate_norm,
            camera_id=read.camera_id,
            ts=ts,
            confidence=read.confidence,
            vehicle_class=read.vehicle_class.value,
            crop_key=read.crop_key,
            track_id=read.track_id,
        )
        state, closed, _alert = advance_dwell(
            self.dwell_states.get(read.plate_norm), obs, thresholds
        )
        self.dwell_states[read.plate_norm] = state
        if closed is None:
            return
        self.dwell_events.append(
            {
                "plate_norm": closed["plate"],
                "camera_id": closed["camera_id"],
                "vehicle_class": closed["vehicle_class"],
                "entry_ts": closed["first_seen"].replace(tzinfo=None),
                "exit_ts": closed["last_seen"].replace(tzinfo=None),
                "dwell_s": float(closed["dwell_s"]),
                "classification": closed["classification"],
                "confidence": float(closed["confidence"]),
                "crop_key": closed["crop_key"],
            }
        )


class AnalyticsWorker:
    def __init__(self, settings: Settings | None = None, health_port: int = 8082) -> None:
        self.settings = settings or get_settings()
        self.health_port = health_port
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._health = HealthServer("analytics", health_port)
        self._stop = asyncio.Event()
        self._engine: AnalyticsEngine | None = None
        self._cameras: dict[str, CameraInfo] = {}
        self._ch: ClickHouseClient | None = None
        self._redis: Any | None = None
        self._pg_pool = None

    async def _init(self) -> None:
        self._pg_pool = await create_pg_pool(self.settings)
        self._cameras = await load_cameras(self._pg_pool)
        pairs = await load_camera_pairs(self._pg_pool)
        self._ch = ClickHouseClient(self.settings)
        await self._ch.ensure_schema_async()
        free_flow = self._ch.query_free_flow_speeds()
        baselines = self._ch.query_camera_volume_baselines()
        self._engine = AnalyticsEngine(self.settings)
        self._engine.set_topology(pairs, free_flow, baselines)
        self._redis = await create_redis(self.settings)

    def _h3_for_read(self, read: PlateRead) -> int:
        cam = self._cameras.get(read.camera_id)
        if cam is None:
            return 0
        _, h3_r7 = enrich_h3(cam.lat, cam.lng, self.settings)
        return h3_r7

    async def _publish_flow(self, fw: FlowWindow) -> None:
        assert self._producer is not None and self._redis is not None
        payload = fw.model_dump_json().encode()
        await self._producer.send_and_wait(self.settings.topic_flow, payload)
        await self._redis.publish("flow", fw.model_dump_json())

    async def _flush_aggregates(self) -> None:
        assert self._engine is not None and self._ch is not None
        self._engine.close_idle_trips()
        flow_map: dict[str, int] = {}
        how = _hour_of_week(self._engine.watermark) if self._engine.watermark else 0
        for fw in self._engine.flush_closed_windows():
            flow_map[fw.camera_id] = flow_map.get(fw.camera_id, 0) + fw.volume
            await self._publish_flow(fw)
            await self._ch.insert_flow_5min_async(
                [
                    {
                        "camera_id": fw.camera_id,
                        "lane": fw.lane,
                        "window_start": fw.window_start,
                        "counts_car": fw.counts_by_class.get(VehicleClass.car.value, 0),
                        "counts_motorcycle": fw.counts_by_class.get(
                            VehicleClass.motorcycle.value, 0
                        ),
                        "counts_bus": fw.counts_by_class.get(VehicleClass.bus.value, 0),
                        "counts_truck": fw.counts_by_class.get(VehicleClass.truck.value, 0),
                        "counts_auto": fw.counts_by_class.get(VehicleClass.auto.value, 0),
                        "counts_other": fw.counts_by_class.get(VehicleClass.other.value, 0),
                        "avg_speed_kmh": fw.avg_speed_kmh,
                        "volume": fw.volume,
                    }
                ]
            )
        seg_rows = self._engine.aggregate_segments()
        if seg_rows:
            await self._ch.insert_segment_speed_async(seg_rows)
        od_rows = self._engine.drain_od()
        if od_rows:
            await self._ch.insert_od_hourly_async(od_rows)
        camera_od = self._engine.drain_od_camera()
        if camera_od:
            await self._ch.insert_od_camera_async(camera_od)
        dwell_rows = self._engine.drain_dwell()
        if dwell_rows:
            await self._ch.insert_dwell_events_async(dwell_rows)
        travel_rows = self._engine.drain_travel()
        if travel_rows:
            await self._ch.insert_travel_stats_async(travel_rows)
        if flow_map:
            found = self._engine.detect_bottlenecks(flow_map, how=how)
            if found and self._redis is not None:
                import orjson as _orjson

                await self._redis.publish(
                    "bottlenecks",
                    _orjson.dumps(found).decode(),
                )
            if self._engine.anomalies and self._redis is not None:
                import orjson as _orjson

                payload = self._engine.anomalies[-20:]
                await self._redis.publish(
                    "anomalies",
                    _orjson.dumps(payload).decode(),
                )
                self._engine.anomalies.clear()

    async def _tick_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(30)
            await self._flush_aggregates()

    async def _consume_loop(self) -> None:
        assert self._consumer is not None and self._engine is not None
        async for msg in self._consumer:
            if self._stop.is_set():
                break
            try:
                data = orjson.loads(msg.value)
                read = PlateRead.model_validate(data)
            except (ValidationError, orjson.JSONDecodeError):
                continue
            h3_r7 = self._h3_for_read(read)
            for fw in self._engine.process_read(read, h3_r7):
                await self._publish_flow(fw)

    async def run(self) -> None:
        logging.basicConfig(level=logging.INFO)
        await self._health.start()
        await self._init()

        self._consumer = AIOKafkaConsumer(
            self.settings.topic_reads,
            bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="anpr-analytics",
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap,
        )
        await self._consumer.start()
        await self._producer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        tick = asyncio.create_task(self._tick_loop())
        try:
            await self._consume_loop()
        finally:
            self._stop.set()
            tick.cancel()
            try:
                await tick
            except asyncio.CancelledError:
                pass
            await self._flush_aggregates()
            if self._consumer:
                await self._consumer.stop()
            if self._producer:
                await self._producer.stop()
            if self._pg_pool:
                await self._pg_pool.close()
            if self._redis:
                await self._redis.aclose()
            await self._health.stop()
