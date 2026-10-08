"""Unit tests for alert rules using synthetic PlateReads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from anpr_common.schemas import AlertType, Direction, PlateFormat, PlateRead, VehicleClass
from workers.alerts.engine import AlertsWorker
from workers.alerts.registry import FileRegistryClient, RegistryRecord
from workers.alerts.rules import (
    ALERT_DEDUP_MINUTES,
    AlertDeduper,
    ClonedPlateRule,
    ConvoyRule,
    DefaultRuleContext,
    GeofenceRule,
    PlateVehicleMismatchRule,
    RedisAlertDeduper,
    RouteAnomalyRule,
    WatchlistRule,
    WrongWayRule,
)
from workers.db import CameraInfo


def _read(
    plate: str = "BR01AB1234",
    camera: str = "cam-1",
    ts: datetime | None = None,
    confidence: float = 0.95,
    direction: Direction | None = Direction.N,
    vehicle_class: VehicleClass = VehicleClass.car,
) -> PlateRead:
    return PlateRead(
        camera_id=camera,
        ts=ts or datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        plate_raw=plate,
        plate_norm=plate,
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=confidence,
        direction=direction,
        vehicle_class=vehicle_class,
        source="simulator",
    )


def _ctx(**overrides):
    base = {
        "settings": SimpleNamespace(max_urban_speed_kmh=120.0),
        "cameras": {
            "cam-1": CameraInfo("cam-1", 18.5, 73.8, 0.0, "N"),
            "cam-2": CameraInfo("cam-2", 18.6, 73.9, 90.0, "E"),
        },
        "watchlist": {"WL01AB9999"},
        "pair_distances": {("cam-1", "cam-2"): 5000.0},
        "last_seen": AsyncMock(return_value=None),
        "convoy_peers": AsyncMock(return_value=set()),
        "sensitive_zone_id": AsyncMock(return_value=None),
        "restricted_zone": AsyncMock(return_value=None),
        "plate_sightings_in_zone": AsyncMock(return_value=0),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_watchlist_exact_match():
    rule = WatchlistRule()
    alerts = await rule.evaluate(_read(plate="WL01AB9999"), _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.watchlist
    assert alerts[0].severity.value == "high"
    assert alerts[0].needs_verification is False


@pytest.mark.asyncio
async def test_watchlist_fuzzy_match():
    rule = WatchlistRule()
    alerts = await rule.evaluate(_read(plate="WL01AB9998"), _ctx())
    assert len(alerts) == 1
    assert alerts[0].severity.value == "medium"
    assert alerts[0].needs_verification is True


@pytest.mark.asyncio
async def test_watchlist_ignores_low_confidence_reads():
    rule = WatchlistRule()
    assert await rule.evaluate(_read(plate="WL01AB9999", confidence=0.3), _ctx()) == []
    assert await rule.evaluate(_read(plate="WL01AB9998", confidence=0.3), _ctx()) == []


@pytest.mark.asyncio
async def test_watchlist_min_conf_comes_from_settings():
    ctx = _ctx(settings=SimpleNamespace(max_urban_speed_kmh=120.0, watchlist_min_conf=0.9))
    alerts = await WatchlistRule().evaluate(_read(plate="WL01AB9999", confidence=0.85), ctx)
    assert alerts == []


@pytest.mark.asyncio
async def test_cloned_plate_impossible_speed():
    prev_ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    ctx = _ctx(
        last_seen=AsyncMock(return_value=("cam-1", prev_ts, 0.9)),
    )
    rule = ClonedPlateRule()
    read = _read(camera="cam-2", ts=prev_ts + timedelta(seconds=30), confidence=0.9)
    alerts = await rule.evaluate(read, ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.cloned_plate


@pytest.mark.asyncio
async def test_cloned_plate_rejects_low_confidence():
    prev_ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    ctx = _ctx(last_seen=AsyncMock(return_value=("cam-1", prev_ts, 0.5)))
    rule = ClonedPlateRule()
    read = _read(camera="cam-2", ts=prev_ts + timedelta(seconds=10), confidence=0.9)
    alerts = await rule.evaluate(read, ctx)
    assert alerts == []


@pytest.mark.asyncio
async def test_wrong_way_opposite_direction():
    rule = WrongWayRule()
    read = _read(direction=Direction.S)
    alerts = await rule.evaluate(read, _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.wrong_way


@pytest.mark.asyncio
async def test_geofence_outside_active_hours():
    rule = GeofenceRule()
    ctx = _ctx(
        restricted_zone=AsyncMock(return_value=("zone-r1", {"mon": [{"start": 8, "end": 18}]}))
    )
    read = _read(ts=datetime(2025, 6, 2, 22, 0, tzinfo=UTC))
    alerts = await rule.evaluate(read, ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.geofence


@pytest.mark.asyncio
async def test_plate_vehicle_mismatch_with_registry():
    class MockRegistry:
        async def lookup(self, plate_norm: str) -> RegistryRecord:
            return RegistryRecord(
                plate_norm=plate_norm,
                vehicle_class=VehicleClass.truck,
                status="found",
            )

    rule = PlateVehicleMismatchRule(MockRegistry())
    alerts = await rule.evaluate(_read(vehicle_class=VehicleClass.car), _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.plate_vehicle_mismatch


@pytest.mark.asyncio
async def test_route_anomaly_far_from_corridor(tmp_path):
    # Build redis-like mock with zset behaviour.
    store: dict[str, dict[str, float]] = {}

    class FakeRedis:
        async def zadd(self, key, mapping):
            store.setdefault(key, {}).update(mapping)

        async def expire(self, key, _ttl):
            return True

        async def zrange(self, key, _start, _end):
            return list(store.get(key, {}).keys())

    cameras = {
        "cam-1": CameraInfo("cam-1", 18.50, 73.80, 0.0, "N"),
        "cam-2": CameraInfo("cam-2", 18.51, 73.81, 90.0, "E"),
        "cam-3": CameraInfo("cam-3", 18.52, 73.82, 180.0, "S"),
        "cam-far": CameraInfo("cam-far", 18.70, 74.10, 0.0, "N"),  # ~30km away
    }
    # Seed prior cameras in redis.
    key = "plate:cams:BR01AB1234"
    store[key] = {"cam-1": 1.0, "cam-2": 2.0, "cam-3": 3.0}
    ctx = _ctx(
        cameras=cameras,
        redis=FakeRedis(),
        settings=SimpleNamespace(
            max_urban_speed_kmh=120.0,
            route_anomaly_min_cameras=3,
            route_anomaly_distance_m=8000.0,
        ),
        last_seen=AsyncMock(return_value=("cam-3", datetime(2025, 6, 1, 12, 0, tzinfo=UTC), 0.9)),
        pair_distances={},
    )
    rule = RouteAnomalyRule()
    alerts = await rule.evaluate(_read(camera="cam-far"), ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.route_anomaly


@pytest.mark.asyncio
async def test_file_registry_client(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text('{"MH12XY9999": {"vehicle_class": "truck", "color": "blue"}}')
    client = FileRegistryClient(path)
    found = await client.lookup("MH12XY9999")
    missing = await client.lookup("UNKNOWN1")
    assert found.status == "found"
    assert found.vehicle_class == VehicleClass.truck
    assert missing.status == "unknown"


@pytest.mark.asyncio
async def test_watchlist_exact_includes_crop_key():
    read = PlateRead(
        camera_id="cam-1",
        ts=datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        plate_raw="WL01AB9999",
        plate_norm="WL01AB9999",
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.95,
        crop_key="cam-1/abc.jpg",
        source="simulator",
    )
    alerts = await WatchlistRule().evaluate(read, _ctx())
    assert alerts[0].evidence["reads"][0]["crop_key"] == "cam-1/abc.jpg"


@pytest.mark.asyncio
async def test_cloned_plate_respects_camera_speed_limit():
    cameras = {
        "cam-1": CameraInfo("cam-1", 18.5, 73.8, 0.0, "N", speed_limit_kmh=120.0),
        "cam-2": CameraInfo("cam-2", 18.51, 73.81, 90.0, "E", speed_limit_kmh=120.0),
    }
    # 1500 m in 36 s is 150 km/h. Posted limits of 120 raise the clone threshold to 180.
    ctx = _ctx(
        cameras=cameras,
        settings=SimpleNamespace(max_urban_speed_kmh=80.0),
        pair_distances={("cam-1", "cam-2"): 1500.0},
        last_seen=AsyncMock(
            return_value=("cam-1", datetime(2025, 6, 1, 11, 59, 24, tzinfo=UTC), 0.95)
        ),
    )
    alerts = await ClonedPlateRule().evaluate(_read(camera="cam-2"), ctx)
    assert alerts == []


def test_alert_deduper_within_10_minutes():
    deduper = AlertDeduper()
    from anpr_common.schemas import Alert, AlertSeverity

    a1 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
    )
    a2 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 5, tzinfo=UTC),
    )
    assert deduper.is_duplicate(a1, datetime(2025, 1, 1, 10, 0, tzinfo=UTC)) is False
    assert deduper.is_duplicate(a2, datetime(2025, 1, 1, 10, 5, tzinfo=UTC)) is True
    a3 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 11, tzinfo=UTC),
    )
    assert deduper.is_duplicate(a3, datetime(2025, 1, 1, 10, 11, tzinfo=UTC)) is False


class _SharedRedis:
    """In-memory SET NX EX shared by two alert-worker replicas."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.calls: list[dict] = []

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        self.calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.held:
            return 0
        self.held.add(key)
        return 1


@pytest.mark.asyncio
async def test_redis_alert_deduper_is_shared_across_replicas():
    from anpr_common.schemas import Alert, AlertSeverity

    redis = _SharedRedis()
    left = RedisAlertDeduper(redis)
    right = RedisAlertDeduper(redis)
    alert = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="br01ab1234",
        ts=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
    )
    assert await left.is_duplicate(alert) is False
    assert await right.is_duplicate(alert) is True
    assert redis.calls[0]["key"] == "alert:dedup:watchlist:BR01AB1234"
    assert redis.calls[0]["nx"] is True
    assert redis.calls[0]["ex"] == ALERT_DEDUP_MINUTES * 60
    assert redis.calls[1]["ex"] == ALERT_DEDUP_MINUTES * 60


class _ConvoyRedis:
    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, _ttl: int) -> bool:
        return True

    async def zrangebyscore(self, key: str, min_s, max_s):
        lo = float("-inf") if min_s in ("-inf", None) else float(min_s)
        hi = float("inf") if max_s in ("+inf", None) else float(max_s)
        return [m for m, s in self.zsets.get(key, {}).items() if lo <= float(s) <= hi]

    async def zscore(self, key: str, member: str) -> float | None:
        return self.zsets.get(key, {}).get(member)

    def pipeline(self, transaction: bool = False):
        return _ConvoyPipe(self)


class _ConvoyPipe:
    def __init__(self, redis: _ConvoyRedis) -> None:
        self.redis = redis
        self.ops: list[tuple] = []

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self.ops:
            if op[0] == "zadd":
                await self.redis.zadd(op[1], op[2])
            else:
                await self.redis.expire(op[1], op[2])


@pytest.mark.asyncio
async def test_convoy_rule_excludes_self_after_prime():
    """Plate A is written into convoy state before evaluation and must not alert as its own peer."""
    redis = _ConvoyRedis()
    ctx = DefaultRuleContext(
        settings=SimpleNamespace(max_urban_speed_kmh=120.0),
        cameras={"cam-1": CameraInfo("cam-1", 18.5, 73.8, 0.0, "N")},
        watchlist=set(),
        pairs=[],
        redis=redis,
        pg_pool=None,
    )
    read = _read(plate="MH01AA0001", camera="cam-1")
    await AlertsWorker._prime_convoy_state(SimpleNamespace(_redis=redis), read)
    peers = await ctx.convoy_peers(read.plate_norm, read.camera_id, read.ts)
    assert read.plate_norm.upper() not in peers
    alerts = await ConvoyRule().evaluate(read, ctx)
    assert alerts == []
