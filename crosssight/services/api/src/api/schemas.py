"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from api.auth import Role


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    username: str


class CameraOut(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    heading_deg: float
    lanes: int
    allowed_direction: str | None
    osm_u: int | None
    osm_v: int | None
    status: str
    has_live_frame: bool = False


class CameraCreate(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    heading_deg: float = 0.0
    lanes: int = 2
    allowed_direction: str | None = None
    osm_u: int | None = None
    osm_v: int | None = None
    status: str = "active"


class CameraUpdate(BaseModel):
    name: str | None = None
    lat: float | None = None
    lng: float | None = None
    heading_deg: float | None = None
    lanes: int | None = None
    allowed_direction: str | None = None
    osm_u: int | None = None
    osm_v: int | None = None
    status: str | None = None


class ZoneOut(BaseModel):
    id: str
    name: str
    kind: Literal["sensitive", "restricted", "ward"]
    active_hours: dict[str, Any] | None
    geojson: dict[str, Any]


class ZoneCreate(BaseModel):
    id: str
    name: str
    kind: Literal["sensitive", "restricted", "ward"]
    geojson: dict[str, Any]
    active_hours: dict[str, Any] | None = None


class ZoneUpdate(BaseModel):
    name: str | None = None
    kind: Literal["sensitive", "restricted", "ward"] | None = None
    geojson: dict[str, Any] | None = None
    active_hours: dict[str, Any] | None = None


class WatchlistOut(BaseModel):
    plate_norm: str
    reason: str
    severity: str
    added_by: str
    expires_at: datetime | None
    created_at: datetime | None = None


class WatchlistCreate(BaseModel):
    plate_norm: str
    reason: str
    severity: str = "high"
    expires_at: datetime | None = None


class AlertOut(BaseModel):
    id: UUID
    type: str
    severity: str
    plate_norm: str
    camera_ids: list[str]
    evidence: dict[str, Any]
    status: str
    needs_verification: bool
    ack_by: str | None
    dispatched_to: str | None
    closed_note: str | None
    created_at: datetime
    updated_at: datetime
    crop_url: str | None = None


class AlertActionBody(BaseModel):
    note: str | None = None


class DispatchBody(BaseModel):
    dispatched_to: str


class ReviewBody(BaseModel):
    status: Literal["reviewing", "approved", "dismissed", "closed"]
    note: str | None = None


class AuditEntryOut(BaseModel):
    id: int
    user_id: str | None
    action: str
    plate_norm: str | None
    case_id: str | None
    params: dict[str, Any]
    ts: datetime


class TrajectorySummary(BaseModel):
    plate_norm: str
    distance_m: float
    duration_s: float
    camera_count: int
    read_count: int
    impossible_hop_count: int
    inferred_leg_count: int
    data_status: Literal["narrow", "widened_to_all_history"] = "narrow"


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict[str, Any]]
    summary: TrajectorySummary
