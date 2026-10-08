"""Camera OD and travel statistics produced by the analytics engine."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from anpr_common.config import Settings
from anpr_common.schemas import PlateFormat, PlateRead, VehicleClass
from workers.analytics import AnalyticsEngine
from workers.db import CameraPair


def _read(camera: str, minutes: int, plate: str = "MH12AB1234") -> PlateRead:
    return PlateRead(
        camera_id=camera,
        ts=datetime(2026, 6, 1, 8, 0, tzinfo=UTC) + timedelta(minutes=minutes),
        plate_raw=plate,
        plate_norm=plate,
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.9,
        vehicle_class=VehicleClass.car,
        source="simulator",
        speed_kmh=40,
    )


def _engine() -> AnalyticsEngine:
    settings = SimpleNamespace(
        trip_gap_min=30,
        max_urban_speed_kmh=120,
        stopped_short_s=45,
        stopped_excessive_s=120,
        stopped_incident_s=180,
        stopped_gap_s=90,
    )
    engine = AnalyticsEngine(cast(Settings, settings))
    engine.set_topology(
        [CameraPair("cam-a", "cam-b", 1000.0, True)],
        {},
        {},
    )
    return engine


def test_camera_od_closes_on_trip_gap():
    engine = _engine()
    engine.process_read(_read("cam-a", 0), 1)
    engine.process_read(_read("cam-b", 5), 2)
    engine.process_read(_read("cam-c", 50), 3)
    rows = engine.drain_od_camera()
    assert rows[0]["origin_camera"] == "cam-a"
    assert rows[0]["dest_camera"] == "cam-b"
    assert rows[0]["trip_count"] == 1


def test_impossible_travel_is_excluded_from_stats():
    engine = _engine()
    engine.process_read(_read("cam-a", 0), 1)
    engine.process_read(_read("cam-b", 0), 2)
    engine.aggregate_segments()
    assert engine.drain_travel() == []
