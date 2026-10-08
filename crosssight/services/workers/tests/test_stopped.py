"""Stopped-vehicle rule stays separate from loitering."""

from datetime import UTC, datetime

import pytest
from anpr_common.schemas import AlertType, PlateFormat, PlateRead, VehicleClass
from workers.alerts.rules import StoppedVehicleRule


def _read() -> PlateRead:
    return PlateRead(
        camera_id="cam-1",
        ts=datetime(2026, 6, 1, tzinfo=UTC),
        plate_raw="MH12AB1234",
        plate_norm="MH12AB1234",
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.9,
        vehicle_class=VehicleClass.car,
        source="simulator",
        track_id=4,
        crop_key="crops/x.jpg",
    )


@pytest.mark.asyncio
async def test_stopped_vehicle_emits_when_dwell_crosses_incident():
    class Ctx:
        async def observe_dwell(self, read):
            return {
                "should_alert": True,
                "evidence": {
                    "dwell_s": 200,
                    "classification": "stopped_vehicle",
                    "first_seen": read.ts.isoformat(),
                    "camera_id": read.camera_id,
                },
            }

    alerts = await StoppedVehicleRule().evaluate(_read(), Ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.stopped_vehicle
    assert alerts[0].evidence["dwell_s"] == 200
    assert alerts[0].evidence["reads"][0]["crop_key"] == "crops/x.jpg"


@pytest.mark.asyncio
async def test_short_stop_does_not_alert_and_loitering_type_is_unchanged():
    class Ctx:
        async def observe_dwell(self, read):
            return {"should_alert": False, "evidence": {"classification": "short_stop"}}

    assert await StoppedVehicleRule().evaluate(_read(), Ctx()) == []
    assert AlertType.loitering.value == "loitering"
