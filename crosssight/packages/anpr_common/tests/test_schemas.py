"""Schema round-trip and JSON Schema export tests."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from anpr_common.schemas import Alert, FlowWindow, PlateRead, export_json_schemas
from pydantic import ValidationError


def test_plate_read_roundtrip():
    pr = PlateRead(
        event_id=uuid4(),
        camera_id="cam-001",
        ts=datetime.now(UTC),
        plate_raw="MH 12 DE 1433",
        plate_norm="mh12de1433",
        plate_valid=True,
        plate_format="standard",
        confidence=0.91,
        char_conf=[0.9] * 10,
        alternates=["MH12DE1433"],
        lane=1,
        direction="N",
        vehicle_class="car",
        color="white",
        source="simulator",
    )
    data = pr.model_dump(mode="json")
    restored = PlateRead.model_validate(data)
    assert restored.plate_norm == "MH12DE1433"
    assert restored.camera_id == "cam-001"
    assert restored.confidence == 0.91


def test_alert_roundtrip():
    a = Alert(
        type="watchlist",
        severity="high",
        plate_norm="MH12DE1433",
        camera_ids=["cam-001"],
        evidence={"reads": []},
    )
    restored = Alert.model_validate(a.model_dump(mode="json"))
    assert restored.type.value == "watchlist"


def test_flow_window_roundtrip():
    fw = FlowWindow(
        camera_id="cam-001",
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 1, 1, 0, 5, tzinfo=UTC),
        counts_by_class={"car": 10},
        volume=10,
    )
    restored = FlowWindow.model_validate_json(fw.model_dump_json())
    assert restored.volume == 10


def test_bbox_must_be_four_floats():
    with pytest.raises(ValidationError):
        PlateRead(
            camera_id="cam-001",
            ts=datetime.now(UTC),
            plate_raw="MH12DE1433",
            plate_norm="MH12DE1433",
            plate_valid=True,
            plate_format="standard",
            confidence=0.9,
            bbox=(1.0, 2.0, 3.0),
        )


def test_export_json_schemas(tmp_path):
    paths = export_json_schemas(tmp_path)
    assert len(paths) == 3
    for p in paths:
        schema = json.loads(p.read_text())
        assert "properties" in schema or "$defs" in schema or "title" in schema
