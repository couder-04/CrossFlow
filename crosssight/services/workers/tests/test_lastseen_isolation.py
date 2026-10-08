"""The clone rule must not read ``lastseen`` state that the ingest worker overwrites.

Ingest and alerts are separate consumers of ``anpr.reads.v1``. Ingest stamps
``lastseen:{plate}`` with the *current* read as soon as it is stored. If the
clone rule read that same key, a fast ingest worker would replace the previous
sighting with the read under evaluation and the impossible-transit alert would
be silently skipped (same camera -> no alert). Seen live: both injected clone
scenarios were missed on a clean ``make workers`` + ``make simulate`` run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from anpr_common.schemas import AlertType, PlateFormat, PlateRead
from workers.alerts.engine import AlertsWorker
from workers.alerts.rules import ClonedPlateRule, DefaultRuleContext
from workers.db import CameraInfo

PLATE = "GJ116601"
T0 = datetime(2026, 10, 8, 9, 0, 0, tzinfo=UTC)


class _Redis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _read(camera: str, ts: datetime) -> PlateRead:
    return PlateRead(
        camera_id=camera,
        ts=ts,
        plate_raw=PLATE,
        plate_norm=PLATE,
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.9,
    )


def _ctx(redis: _Redis) -> DefaultRuleContext:
    return DefaultRuleContext(
        SimpleNamespace(max_urban_speed_kmh=120.0),
        {
            "cam-a": CameraInfo("cam-a", 18.488, 73.858, 180.0, "S"),
            "cam-b": CameraInfo("cam-b", 18.540, 73.826, 0.0, "N"),
        },
        set(),
        [],
        redis,
        None,
    )


async def _ingest_stamp(redis: _Redis, read: PlateRead) -> None:
    """What workers.ingest does after storing a read (kept in sync by the assert below)."""
    await redis.hset(
        f"lastseen:{read.plate_norm.upper()}",
        mapping={
            "camera_id": read.camera_id,
            "ts": read.ts.isoformat(),
            "confidence": str(read.confidence),
        },
    )


@pytest.mark.asyncio
async def test_clone_alert_survives_ingest_overwriting_lastseen():
    redis = _Redis()
    ctx = _ctx(redis)
    worker = AlertsWorker(health_port=0)
    worker._redis = redis
    rule = ClonedPlateRule()

    a = _read("cam-a", T0)
    b = _read("cam-b", T0 + timedelta(seconds=70))  # ~7 km in 70 s

    assert await rule.evaluate(a, ctx) == []
    await worker._record_last_seen(a)
    # Ingest is faster than alerts and has already stamped read B.
    await _ingest_stamp(redis, a)
    await _ingest_stamp(redis, b)

    alerts = await rule.evaluate(b, ctx)
    assert [al.type for al in alerts] == [AlertType.cloned_plate]


def test_ingest_still_writes_its_own_lastseen_key():
    from pathlib import Path

    from workers import ingest

    src = Path(ingest.__file__).read_text()
    assert 'f"lastseen:{plate}"' in src
    assert 'f"alerts:lastseen:{plate}"' not in src
