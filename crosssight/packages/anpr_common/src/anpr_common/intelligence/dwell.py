"""Stationary-vehicle dwell classification.

Loitering stays a sensitive-zone sighting count. This module measures how long
one vehicle remains at one camera and separates a short stop, excessive dwell,
and a stopped-vehicle incident.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum


class DwellClass(str, Enum):
    short_stop = "short_stop"
    excessive_dwell = "excessive_dwell"
    stopped_vehicle = "stopped_vehicle"


@dataclass(frozen=True)
class DwellThresholds:
    short_s: float = 45.0
    excessive_s: float = 120.0
    incident_s: float = 180.0
    gap_s: float = 90.0

    def __post_init__(self) -> None:
        if not (0 <= self.short_s <= self.excessive_s <= self.incident_s):
            raise ValueError("dwell thresholds must satisfy 0 <= short <= excessive <= incident")
        if self.gap_s <= 0:
            raise ValueError("dwell gap must be positive")


def thresholds_for_camera(
    camera_id: str,
    defaults: DwellThresholds,
    overrides: dict[str, dict] | None = None,
) -> DwellThresholds:
    raw = (overrides or {}).get(camera_id) or {}
    if not raw:
        return defaults
    return DwellThresholds(
        short_s=float(raw.get("short_s", defaults.short_s)),
        excessive_s=float(raw.get("excessive_s", defaults.excessive_s)),
        incident_s=float(raw.get("incident_s", defaults.incident_s)),
        gap_s=float(raw.get("gap_s", defaults.gap_s)),
    )


def classify_dwell(duration_s: float, thresholds: DwellThresholds) -> DwellClass:
    """Map a same-camera stay onto short stop, excessive dwell, or an incident.

    Stops shorter than ``excessive_s`` stay normal. The incident threshold is
    what raises STOPPED_VEHICLE; loitering is a separate rule.
    """
    if duration_s < 0:
        raise ValueError("dwell duration cannot be negative")
    if duration_s < thresholds.excessive_s:
        return DwellClass.short_stop
    if duration_s < thresholds.incident_s:
        return DwellClass.excessive_dwell
    return DwellClass.stopped_vehicle


def dwell_anomaly(actual_s: float, expected_s: float | None, tolerance: float = 0.5) -> dict | None:
    """Compare a stay with an expected dwell. None when no expectation is configured."""
    if expected_s is None or expected_s <= 0 or actual_s < 0:
        return None
    ratio = actual_s / expected_s
    if ratio > 1.0 + tolerance:
        kind = "long"
    elif ratio < max(0.0, 1.0 - tolerance):
        kind = "short"
    else:
        kind = "normal"
    return {"kind": kind, "actual_s": actual_s, "expected_s": expected_s, "ratio": ratio}


@dataclass(frozen=True)
class DwellState:
    vehicle_id: str
    plate: str
    camera_id: str
    zone_id: str | None
    first_seen: datetime
    last_seen: datetime
    sightings: int
    confidence: float
    crop_key: str | None
    vehicle_class: str
    lat: float | None = None
    lng: float | None = None
    track_id: int | None = None
    alerted: bool = False

    @property
    def dwell_s(self) -> float:
        return max(0.0, (self.last_seen - self.first_seen).total_seconds())


@dataclass(frozen=True)
class DwellObservation:
    vehicle_id: str
    plate: str
    camera_id: str
    ts: datetime
    confidence: float
    vehicle_class: str
    zone_id: str | None = None
    crop_key: str | None = None
    lat: float | None = None
    lng: float | None = None
    track_id: int | None = None


def _event(state: DwellState, thresholds: DwellThresholds) -> dict:
    kind = classify_dwell(state.dwell_s, thresholds)
    return {
        "vehicle_id": state.vehicle_id,
        "plate": state.plate,
        "camera_id": state.camera_id,
        "zone_id": state.zone_id,
        "first_seen": state.first_seen,
        "last_seen": state.last_seen,
        "dwell_s": state.dwell_s,
        "classification": kind.value,
        "confidence": state.confidence,
        "crop_key": state.crop_key,
        "vehicle_class": state.vehicle_class,
        "lat": state.lat,
        "lng": state.lng,
        "track_id": state.track_id,
        "sightings": state.sightings,
    }


def advance_dwell(
    state: DwellState | None,
    obs: DwellObservation,
    thresholds: DwellThresholds,
) -> tuple[DwellState, dict | None, bool]:
    """Fold one observation into a same-camera dwell.

    Returns the open state, a closed dwell event when the vehicle left or the
    gap was too long, and whether a new stopped-vehicle incident should fire.
    """
    closed: dict | None = None
    if state is None or state.camera_id != obs.camera_id or state.vehicle_id != obs.vehicle_id:
        if state is not None and state.dwell_s > 0:
            closed = _event(state, thresholds)
        opened = DwellState(
            vehicle_id=obs.vehicle_id,
            plate=obs.plate,
            camera_id=obs.camera_id,
            zone_id=obs.zone_id,
            first_seen=obs.ts,
            last_seen=obs.ts,
            sightings=1,
            confidence=obs.confidence,
            crop_key=obs.crop_key,
            vehicle_class=obs.vehicle_class,
            lat=obs.lat,
            lng=obs.lng,
            track_id=obs.track_id,
        )
        return opened, closed, False

    gap = (obs.ts - state.last_seen).total_seconds()
    if gap < 0 or gap > thresholds.gap_s:
        if state.dwell_s > 0:
            closed = _event(state, thresholds)
        opened = DwellState(
            vehicle_id=obs.vehicle_id,
            plate=obs.plate,
            camera_id=obs.camera_id,
            zone_id=obs.zone_id or state.zone_id,
            first_seen=obs.ts,
            last_seen=obs.ts,
            sightings=1,
            confidence=obs.confidence,
            crop_key=obs.crop_key or state.crop_key,
            vehicle_class=obs.vehicle_class,
            lat=obs.lat if obs.lat is not None else state.lat,
            lng=obs.lng if obs.lng is not None else state.lng,
            track_id=obs.track_id,
        )
        return opened, closed, False

    opened = replace(
        state,
        last_seen=obs.ts,
        sightings=state.sightings + 1,
        confidence=max(state.confidence, obs.confidence),
        crop_key=obs.crop_key or state.crop_key,
        zone_id=obs.zone_id or state.zone_id,
        lat=obs.lat if obs.lat is not None else state.lat,
        lng=obs.lng if obs.lng is not None else state.lng,
        track_id=obs.track_id if obs.track_id is not None else state.track_id,
    )
    kind = classify_dwell(opened.dwell_s, thresholds)
    should_alert = kind is DwellClass.stopped_vehicle and not opened.alerted
    if should_alert:
        opened = replace(opened, alerted=True)
    return opened, None, should_alert
