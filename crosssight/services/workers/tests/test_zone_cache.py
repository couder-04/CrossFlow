"""Unit tests for zone cache + optimized convoy indexing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from anpr_common.schemas import AlertType, Direction, PlateFormat, PlateRead, VehicleClass
from workers.alerts.rules import (
    CONVOY_MIN_COMMON_CAMERAS,
    DefaultRuleContext,
    GeofenceRule,
    LoiteringRule,
)
from workers.db import CameraInfo


def _read(
    plate: str = "BR01AB1234",
    camera: str = "cam-1",
    ts: datetime | None = None,
) -> PlateRead:
    return PlateRead(
        camera_id=camera,
        ts=ts or datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        plate_raw=plate,
        plate_norm=plate,
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.95,
        direction=Direction.N,
        vehicle_class=VehicleClass.car,
        source="simulator",
    )


class FakeRedis:
    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, _ttl: int) -> bool:
        return True

    async def zrangebyscore(self, key: str, min_s, max_s):
        lo = float("-inf") if min_s in ("-inf", None) else float(min_s)
        hi = float("inf") if max_s in ("+inf", None) else float(max_s)
        return [m for m, s in self.zsets.get(key, {}).items() if lo <= s <= hi]

    async def zscore(self, key: str, member: str) -> float | None:
        return self.zsets.get(key, {}).get(member)

    async def zremrangebyscore(self, key: str, min_s, max_s) -> int:
        lo = float(min_s)
        hi = float("inf") if max_s in ("+inf", None) else float(max_s)
        before = self.zsets.get(key, {})
        keep = {m: s for m, s in before.items() if not (lo <= s <= hi)}
        removed = len(before) - len(keep)
        self.zsets[key] = keep
        return removed

    async def zcount(self, key: str, min_s, max_s) -> int:
        members = await self.zrangebyscore(key, min_s, max_s)
        return len(members)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def pipeline(self, transaction: bool = False):
        return _Pipe(self)


class _Pipe:
    def __init__(self, r: FakeRedis) -> None:
        self.r = r
        self.ops: list = []

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self.ops:
            if op[0] == "zadd":
                await self.r.zadd(op[1], op[2])
            elif op[0] == "expire":
                await self.r.expire(op[1], op[2])
        return [True] * len(self.ops)


def _ctx(**overrides):
    redis = FakeRedis()
    cameras = {
        "cam-1": CameraInfo("cam-1", 18.50, 73.80, 0.0, "N"),
        "cam-2": CameraInfo("cam-2", 18.51, 73.81, 90.0, "E"),
        "cam-3": CameraInfo("cam-3", 18.52, 73.82, 180.0, "S"),
    }
    base = {
        "settings": SimpleNamespace(max_urban_speed_kmh=120.0),
        "cameras": cameras,
        "watchlist": set(),
        "pairs": [],
        "redis": redis,
        "pg_pool": None,
        "sensitive_zones": {"cam-1": "sensitive-1"},
        "restricted_zones": {"cam-2": ("restricted-1", {"start": "22:00", "end": "06:00"})},
    }
    base.update(overrides)
    return DefaultRuleContext(**base), redis


@pytest.mark.asyncio
async def test_zone_cache_hits_without_pg():
    ctx, _ = _ctx()
    assert await ctx.sensitive_zone_id("cam-1") == "sensitive-1"
    assert await ctx.sensitive_zone_id("cam-2") is None
    zone = await ctx.restricted_zone("cam-2")
    assert zone is not None
    assert zone[0] == "restricted-1"


@pytest.mark.asyncio
async def test_geofence_uses_cached_restricted_zone():
    ctx, _ = _ctx()
    rule = GeofenceRule()
    # 10:00 is outside 22:00–06:00 → alert
    alerts = await rule.evaluate(
        _read(camera="cam-2", ts=datetime(2025, 6, 1, 10, 0, tzinfo=UTC)), ctx
    )
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.geofence


@pytest.mark.asyncio
async def test_loitering_redis_backed_counts():
    ctx, _ = _ctx()
    rule = LoiteringRule()
    plate = "JH01LT0001"
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    fired = False
    for i in range(6):
        alerts = await rule.evaluate(
            _read(plate=plate, camera="cam-1", ts=base + timedelta(minutes=i * 5)), ctx
        )
        if alerts:
            fired = True
            assert alerts[0].type == AlertType.loitering
    assert fired


@pytest.mark.asyncio
async def test_convoy_peers_uses_plate_index():
    ctx, redis = _ctx()
    base = datetime(2025, 6, 1, 12, 0, tzinfo=UTC).timestamp()
    plates = ["AA01CV0001", "BB01CV0002", "CC01CV0003"]
    cams = ["cam-1", "cam-2", "cam-3"]
    for cam in cams:
        for i, plate in enumerate(plates):
            ts = base + i
            await redis.zadd(f"convoy:cam:{cam}", {plate: ts})
            await redis.zadd(f"convoy:plate:{plate}", {cam: ts})
    peers = await ctx.convoy_peers("AA01CV0001", "cam-1", datetime.fromtimestamp(base + 1, tz=UTC))
    assert len(peers) >= CONVOY_MIN_COMMON_CAMERAS - 1  # at least the other two when shared>=3
    assert "BB01CV0002" in peers
    assert "CC01CV0003" in peers
