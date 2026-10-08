"""Origin-destination, travel time, dwell sessions, and vehicle-class aggregates."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

from anpr_common.intelligence.dwell import DwellThresholds, classify_dwell


def aggregate_od(
    journeys: list[tuple[str, str, str, datetime]],
    *,
    bucket: str = "hour",
) -> list[dict]:
    """Aggregate (vehicle_id, origin, destination, ts) into OD cells.

    ``origin`` and ``destination`` are camera or zone identifiers. Unique
    vehicles are counted per cell and time bucket.
    """
    cells: dict[tuple, set[str]] = defaultdict(set)
    for vehicle_id, origin, dest, ts in journeys:
        if not origin or not dest or origin == dest:
            continue
        if bucket == "hour":
            key_time = ts.replace(minute=0, second=0, microsecond=0)
        elif bucket == "day":
            key_time = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError("bucket must be hour or day")
        cells[(origin, dest, key_time)].add(vehicle_id)
    rows = [
        {
            "origin": origin,
            "destination": dest,
            "bucket": key_time,
            "trip_count": len(vehicles),
            "unique_vehicles": len(vehicles),
        }
        for (origin, dest, key_time), vehicles in cells.items()
    ]
    rows.sort(key=lambda r: (r["bucket"], r["origin"], r["destination"]))
    return rows


def journeys_from_sightings(
    sightings: list[tuple[str, str, datetime]],
    *,
    gap_s: float,
) -> list[tuple[str, str, str, datetime]]:
    """Build origin→destination journeys from (vehicle, camera, ts) sightings."""
    grouped: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    for vehicle, camera, ts in sightings:
        grouped[vehicle].append((camera, ts))
    journeys: list[tuple[str, str, str, datetime]] = []
    for vehicle, points in grouped.items():
        points.sort(key=lambda p: p[1])
        if not points:
            continue
        origin = points[0][0]
        prev_cam, prev_ts = points[0]
        for cam, ts in points[1:]:
            gap = (ts - prev_ts).total_seconds()
            if gap > gap_s or gap < 0:
                if origin != prev_cam:
                    journeys.append((vehicle, origin, prev_cam, prev_ts))
                origin = cam
            prev_cam, prev_ts = cam, ts
        if origin != prev_cam:
            journeys.append((vehicle, origin, prev_cam, prev_ts))
    return journeys


def valid_travel(
    elapsed_s: float,
    distance_m: float | None,
    *,
    max_speed_kmh: float,
    min_elapsed_s: float = 1.0,
    max_elapsed_s: float = 3600.0,
) -> dict | None:
    """Return a travel sample, or None when the timestamps are unusable."""
    if elapsed_s < min_elapsed_s or elapsed_s > max_elapsed_s:
        return None
    speed = None
    if distance_m is not None and distance_m >= 0:
        speed = (distance_m / elapsed_s) * 3.6
        if speed > max_speed_kmh:
            return None
    return {"elapsed_s": elapsed_s, "distance_m": distance_m, "speed_kmh": speed}


def travel_statistics(samples: list[dict]) -> dict:
    times = sorted(float(s["elapsed_s"]) for s in samples if s.get("elapsed_s") is not None)
    speeds = [float(s["speed_kmh"]) for s in samples if s.get("speed_kmh") is not None]
    if not times:
        return {
            "count": 0,
            "avg_s": None,
            "median_s": None,
            "min_s": None,
            "max_s": None,
            "p90_s": None,
            "avg_speed_kmh": None,
        }
    return {
        "count": len(times),
        "avg_s": statistics.fmean(times),
        "median_s": statistics.median(times),
        "min_s": times[0],
        "max_s": times[-1],
        "p90_s": _percentile(times, 0.9),
        "avg_speed_kmh": statistics.fmean(speeds) if speeds else None,
    }


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def sessionize_dwell(
    sightings: list[dict],
    thresholds: DwellThresholds,
    *,
    expected_by_camera: dict[str, float] | None = None,
) -> list[dict]:
    """Group ordered sightings into entry/exit dwell sessions.

    Each sighting needs plate, camera_id, and ts. Optional vehicle_id,
    vehicle_class, confidence, zone_id, crop_key.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in sightings:
        key = str(row.get("vehicle_id") or row["plate"])
        grouped[key].append(row)
    expected = expected_by_camera or {}
    sessions: list[dict] = []
    for rows in grouped.values():
        rows.sort(key=lambda r: r["ts"])
        current: dict | None = None
        for row in rows:
            if current is None:
                current = _open_session(row)
                continue
            gap = (row["ts"] - current["exit_ts"]).total_seconds()
            same = row["camera_id"] == current["camera_id"]
            if same and 0 <= gap <= thresholds.gap_s:
                current["exit_ts"] = row["ts"]
                current["confidence"] = max(
                    current["confidence"], float(row.get("confidence") or 0)
                )
                if row.get("crop_key"):
                    current["crop_key"] = row["crop_key"]
                continue
            sessions.append(_close_session(current, thresholds, expected))
            current = _open_session(row)
        if current is not None:
            sessions.append(_close_session(current, thresholds, expected))
    return sessions


def _open_session(row: dict) -> dict:
    return {
        "vehicle_id": str(row.get("vehicle_id") or row["plate"]),
        "plate": row["plate"],
        "camera_id": row["camera_id"],
        "zone_id": row.get("zone_id"),
        "vehicle_class": row.get("vehicle_class") or "other",
        "entry_ts": row["ts"],
        "exit_ts": row["ts"],
        "confidence": float(row.get("confidence") or 0),
        "crop_key": row.get("crop_key"),
    }


def _close_session(session: dict, thresholds: DwellThresholds, expected: dict[str, float]) -> dict:
    dwell_s = max(0.0, (session["exit_ts"] - session["entry_ts"]).total_seconds())
    kind = classify_dwell(dwell_s, thresholds)
    out = dict(session)
    out["dwell_s"] = dwell_s
    out["classification"] = kind.value
    out["anomaly"] = dwell_anomaly_safe(dwell_s, expected.get(session["camera_id"]))
    return out


def dwell_anomaly_safe(actual_s: float, expected_s: float | None) -> dict | None:
    from anpr_common.intelligence.dwell import dwell_anomaly

    return dwell_anomaly(actual_s, expected_s)


def aggregate_vehicle_classes(rows: list[dict]) -> dict:
    """Count vehicle classes that the detector actually emits."""
    totals: dict[str, int] = defaultdict(int)
    by_camera: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_zone: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        klass = str(row.get("vehicle_class") or "other")
        totals[klass] += 1
        cam = row.get("camera_id")
        if cam:
            by_camera[str(cam)][klass] += 1
        zone = row.get("zone_id")
        if zone:
            by_zone[str(zone)][klass] += 1
    total = sum(totals.values())
    shares = {k: (v / total if total else 0.0) for k, v in sorted(totals.items())}
    return {
        "total": total,
        "counts": dict(sorted(totals.items())),
        "shares": shares,
        "by_camera": {k: dict(v) for k, v in sorted(by_camera.items())},
        "by_zone": {k: dict(v) for k, v in sorted(by_zone.items())},
    }
