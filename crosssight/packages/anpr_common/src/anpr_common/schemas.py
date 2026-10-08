"""Pydantic event schemas and JSON Schema export."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)
_bbox_rejects = 0


class Direction(str, Enum):
    N = "N"
    NE = "NE"
    E = "E"
    SE = "SE"
    S = "S"
    SW = "SW"
    W = "W"
    NW = "NW"


class VehicleClass(str, Enum):
    car = "car"
    motorcycle = "motorcycle"
    bus = "bus"
    truck = "truck"
    auto = "auto"
    other = "other"


class PlateFormat(str, Enum):
    standard = "standard"
    bh = "bh"
    nonstandard = "nonstandard"


class AlertType(str, Enum):
    watchlist = "watchlist"
    cloned_plate = "cloned_plate"
    convoy = "convoy"
    loitering = "loitering"
    geofence = "geofence"
    wrong_way = "wrong_way"
    plate_vehicle_mismatch = "plate_vehicle_mismatch"
    route_anomaly = "route_anomaly"
    stopped_vehicle = "stopped_vehicle"
    camera_health = "camera_health"


class AlertSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AlertStatus(str, Enum):
    new = "new"
    acknowledged = "acknowledged"
    dispatched = "dispatched"
    closed = "closed"
    false_positive = "false_positive"
    reviewing = "reviewing"
    approved = "approved"
    dismissed = "dismissed"


class PlateRead(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    camera_id: str
    ts: datetime
    plate_raw: str
    plate_norm: str
    plate_valid: bool
    plate_format: PlateFormat
    confidence: float = Field(ge=0.0, le=1.0)
    char_conf: list[float] = Field(default_factory=list)
    alternates: list[str] = Field(default_factory=list)
    lane: int | None = None
    direction: Direction | None = None
    vehicle_class: VehicleClass = VehicleClass.car
    color: str | None = None
    make: str | None = None
    speed_kmh: float | None = None
    crop_key: str | None = None
    source: Literal["ocr", "simulator"] = "simulator"
    track_id: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_video_key: str | None = None

    @field_validator("bbox", mode="before")
    @classmethod
    def bbox_xyxy(cls, value: Any) -> tuple[float, float, float, float] | None:
        global _bbox_rejects
        if value is None:
            return None
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        _bbox_rejects += 1
        if _bbox_rejects % 1000 == 0:
            logger.info("rejected %s plate bboxes that were not x1,y1,x2,y2", _bbox_rejects)
        raise ValueError("bbox must be x1,y1,x2,y2")

    @field_validator("plate_norm")
    @classmethod
    def uppercase_norm(cls, v: str) -> str:
        return v.upper().replace(" ", "")

    model_config = {"json_schema_extra": {"examples": [{"camera_id": "cam-001"}]}}


class Alert(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: AlertType
    severity: AlertSeverity
    plate_norm: str
    camera_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: AlertStatus = AlertStatus.new
    needs_verification: bool = False
    ts: datetime = Field(default_factory=datetime.utcnow)
    ack_by: str | None = None
    dispatched_to: str | None = None
    closed_note: str | None = None


class FlowWindow(BaseModel):
    camera_id: str
    window_start: datetime
    window_end: datetime
    counts_by_class: dict[str, int] = Field(default_factory=dict)
    avg_speed_kmh: float | None = None
    congestion_index: float | None = None
    volume: int = 0
    lane: int | None = None


def export_json_schemas(out_dir: str | Path) -> list[Path]:
    """Write JSON Schema files for all public models."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in (
        ("PlateRead", PlateRead),
        ("Alert", Alert),
        ("FlowWindow", FlowWindow),
    ):
        path = out / f"{name}.json"
        schema = model.model_json_schema()
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
