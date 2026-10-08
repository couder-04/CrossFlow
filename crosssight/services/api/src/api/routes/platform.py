"""Camera health, OD, travel time, dwell, and vehicle-class analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from anpr_common.intelligence.access import role_can
from anpr_common.intelligence.dwell import DwellThresholds
from anpr_common.intelligence.health import CameraHealthThresholds, classify_camera_health
from anpr_common.intelligence.journeys import aggregate_vehicle_class_counts, sessionize_dwell
from anpr_common.schemas import AlertSeverity, AlertType
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.params import Param
from geoalchemy2.functions import ST_X, ST_Y
from sqlalchemy import select

from api.auditutil import audit_row
from api.db import AlertRow, Camera
from api.deps import ClickHouseDep, OperatorUserDep, SessionDep, SettingsDep, UserDep
from api.video_feeds import normalize_source

router = APIRouter(prefix="/ops", tags=["ops"])


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    finish = end or datetime.now(UTC)
    begin = start or (finish - timedelta(hours=1))
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=UTC)
    if finish.tzinfo is None:
        finish = finish.replace(tzinfo=UTC)
    if begin >= finish:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="start must be before end"
        )
    return begin, finish


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _resolved(value: Any, fallback: Any) -> Any:
    """Return a concrete value when a route function is called directly.

    FastAPI fills Query defaults only for HTTP requests. Export jobs call these
    functions themselves, and a Query object is not a ClickHouse parameter.
    """
    if isinstance(value, Param):
        default = value.default
        return fallback if default is ... else default
    return value


def _query(ch, sql: str, parameters: dict) -> list[tuple]:
    try:
        result = ch.query(sql, parameters=parameters)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"analytics store unavailable: {exc}",
        ) from exc
    return list(result.result_rows)


def _od_cell(
    origin: str,
    destination: str,
    trip_count: int,
    unique_vehicles: int | None,
    k: int,
) -> dict[str, Any] | None:
    """Publish an OD cell only when the vehicle count itself clears k-anonymity.

    ``od_camera_hourly`` stores summed trips, not plate cardinality, so a missing
    vehicle count must not be filled with ``trip_count``.
    """
    if unique_vehicles is None or unique_vehicles < k:
        return None
    return {
        "origin": origin,
        "destination": destination,
        "trip_count": trip_count,
        "unique_vehicles": unique_vehicles,
    }


async def _cameras(session) -> list[dict]:
    result = await session.execute(
        select(Camera, ST_Y(Camera.geom).label("lat"), ST_X(Camera.geom).label("lng"))
    )
    rows = []
    for cam, lat, lng in result.all():
        rows.append(
            {
                "id": cam.id,
                "name": cam.name,
                "status": cam.status,
                "lat": float(lat),
                "lng": float(lng),
                "ops_config": cam.ops_config or {},
            }
        )
    return rows


def _health_for(
    cameras: list[dict],
    last_reads: dict[str, datetime],
    window_counts: dict[str, int],
    now: datetime,
    settings,
) -> list[dict]:
    thresholds = CameraHealthThresholds(
        stale_after_s=settings.health_stale_s,
        offline_after_s=settings.health_offline_s,
        expected_reads_per_min=settings.health_expected_rpm,
        rate_low_ratio=settings.health_rate_low_ratio,
        rate_high_ratio=settings.health_rate_high_ratio,
        window_s=settings.health_window_s,
    )
    output = []
    for cam in cameras:
        last = last_reads.get(cam["id"])
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        classified = classify_camera_health(
            status=cam["status"],
            now=now,
            last_read=last,
            reads_in_window=window_counts.get(cam["id"], 0),
            thresholds=thresholds,
        )
        output.append(
            {
                "camera_id": cam["id"],
                "name": cam["name"],
                "lat": cam["lat"],
                "lng": cam["lng"],
                "camera_status": cam["status"],
                **classified,
                "last_read": classified["last_read"].isoformat()
                if classified["last_read"]
                else None,
            }
        )
    return output


def _read_stats(ch, start: datetime, end: datetime) -> tuple[dict[str, datetime], dict[str, int]]:
    last_rows = _query(
        ch,
        "SELECT camera_id, max(ts) FROM anpr_reads GROUP BY camera_id",
        {},
    )
    window_rows = _query(
        ch,
        """
        SELECT camera_id, count()
        FROM anpr_reads
        WHERE ts >= {start:DateTime} AND ts < {end:DateTime}
        GROUP BY camera_id
        """,
        {"start": _naive(start), "end": _naive(end)},
    )
    last = {}
    for camera_id, ts in last_rows:
        if ts is not None:
            last[str(camera_id)] = ts if getattr(ts, "tzinfo", None) else ts.replace(tzinfo=UTC)
    counts = {str(camera_id): int(count) for camera_id, count in window_rows}
    return last, counts


@router.get("/cameras/health")
async def camera_health(
    ch: ClickHouseDep,
    session: SessionDep,
    settings: SettingsDep,
    _user: UserDep,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    if not role_can(_user.role.value, "view_health"):
        raise HTTPException(status_code=403, detail="Not permitted")
    begin, finish = _window(start, end)
    cameras = await _cameras(session)
    last, counts = _read_stats(ch, begin, finish)
    rows = _health_for(cameras, last, counts, finish, settings)
    summary = {
        state: sum(1 for row in rows if row["state"] == state)
        for state in ("HEALTHY", "DEGRADED", "STALE", "OFFLINE")
    }
    return {
        "from": begin.isoformat(),
        "to": finish.isoformat(),
        "summary": summary,
        "cameras": rows,
    }


@router.post("/cameras/health/scan")
async def scan_camera_health(
    ch: ClickHouseDep,
    session: SessionDep,
    settings: SettingsDep,
    user: OperatorUserDep,
) -> dict[str, Any]:
    finish = datetime.now(UTC)
    begin = finish - timedelta(seconds=settings.health_window_s)
    cameras = await _cameras(session)
    last, counts = _read_stats(ch, begin, finish)
    rows = _health_for(cameras, last, counts, finish, settings)
    created = 0
    for row in rows:
        if row["state"] not in {"OFFLINE", "STALE"}:
            continue
        plate = f"CAM:{row['camera_id']}"
        existing = await session.execute(
            select(AlertRow).where(
                AlertRow.type == AlertType.camera_health.value,
                AlertRow.plate_norm == plate,
                AlertRow.status.notin_(["closed", "dismissed", "false_positive"]),
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        session.add(
            AlertRow(
                id=uuid4(),
                type=AlertType.camera_health.value,
                severity=AlertSeverity.high.value
                if row["state"] == "OFFLINE"
                else AlertSeverity.medium.value,
                plate_norm=plate,
                camera_ids=[row["camera_id"]],
                evidence={
                    "state": row["state"],
                    "last_read": row["last_read"],
                    "age_s": row["age_s"],
                    "read_rate_per_min": row["read_rate_per_min"],
                    "reasons": row["reasons"],
                },
                status="new",
            )
        )
        created += 1
    session.add(audit_row(user, "camera_health_scan", params={"created": created}))
    await session.commit()
    return {"created": created}


@router.get("/od")
async def camera_od(
    ch: ClickHouseDep,
    session: SessionDep,
    settings: SettingsDep,
    _user: UserDep,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    begin, finish = _window(start, end)
    params = {"start": _naive(begin), "end": _naive(finish), "k": settings.od_k_anon}
    rows = _query(
        ch,
        """
        SELECT origin_camera, dest_camera, sum(trip_count) AS trips
        FROM od_camera_hourly
        WHERE hour >= {start:DateTime} AND hour < {end:DateTime}
        GROUP BY origin_camera, dest_camera
        HAVING trips >= {k:UInt32}
        """,
        params,
    )
    # SummingMergeTree rollup has no per-plate state, and converting it to an
    # AggregatingMergeTree uniq column is not safe on a live table. Count
    # plates from raw reads for the same window and join in process.
    if rows:
        counted = _query(
            ch,
            """
            SELECT origin, dest, uniqExact(plate_norm) AS vehicles
            FROM (
                SELECT plate_norm, argMin(camera_id, ts) AS origin, argMax(camera_id, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime} AND ts < {end:DateTime}
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            """,
            params,
        )
        vehicles_by_pair = {(row[0], row[1]): int(row[2]) for row in counted}
        cells = []
        for row in rows:
            cell = _od_cell(
                row[0],
                row[1],
                int(row[2]),
                vehicles_by_pair.get((row[0], row[1])),
                settings.od_k_anon,
            )
            if cell is not None:
                cells.append(cell)
    else:
        derived = _query(
            ch,
            """
            SELECT origin, dest, count() AS trips, uniqExact(plate_norm) AS vehicles
            FROM (
                SELECT plate_norm, argMin(camera_id, ts) AS origin, argMax(camera_id, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime} AND ts < {end:DateTime}
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            HAVING trips >= {k:UInt32}
            """,
            params,
        )
        cells = []
        for row in derived:
            cell = _od_cell(row[0], row[1], int(row[2]), int(row[3]), settings.od_k_anon)
            if cell is not None:
                cells.append(cell)
    cameras = {cam["id"]: cam for cam in await _cameras(session)}
    for cell in cells:
        for end_name in ("origin", "destination"):
            cam = cameras.get(cell[end_name])
            cell[f"{end_name}_lat"] = cam["lat"] if cam else None
            cell[f"{end_name}_lng"] = cam["lng"] if cam else None
    return {
        "from": begin.isoformat(),
        "to": finish.isoformat(),
        "k_anonymity": settings.od_k_anon,
        "cells": cells,
    }


@router.get("/travel")
async def travel_times(
    ch: ClickHouseDep,
    settings: SettingsDep,
    _user: UserDep,
    start: datetime | None = None,
    end: datetime | None = None,
    origin: str | None = None,
    destination: str | None = None,
) -> dict[str, Any]:
    begin, finish = _window(start, end)
    params: dict[str, Any] = {"start": _naive(begin), "end": _naive(finish)}
    where = "window_start >= {start:DateTime} AND window_start < {end:DateTime}"
    if origin:
        where += " AND camera_a = {origin:String}"
        params["origin"] = origin
    if destination:
        where += " AND camera_b = {destination:String}"
        params["destination"] = destination
    rows = _query(
        ch,
        f"""
        SELECT camera_a, camera_b,
               sum(sample_count), avg(avg_travel_s), avg(median_travel_s),
               min(min_travel_s), max(max_travel_s), avg(p90_travel_s), avg(avg_speed_kmh)
        FROM travel_stats_5min
        WHERE {where}
        GROUP BY camera_a, camera_b
        """,
        params,
    )
    if not rows:
        rows = _query(
            ch,
            f"""
            SELECT camera_a, camera_b,
                   sum(sample_count), avg(median_travel_s), median(median_travel_s),
                   min(median_travel_s), max(median_travel_s), quantile(0.9)(median_travel_s),
                   avg(median_speed_kmh)
            FROM segment_speed_5min
            WHERE {where}
            GROUP BY camera_a, camera_b
            """,
            params,
        )
    routes = [
        {
            "origin": row[0],
            "destination": row[1],
            "count": int(row[2] or 0),
            "avg_s": float(row[3]) if row[3] is not None else None,
            "median_s": float(row[4]) if row[4] is not None else None,
            "min_s": float(row[5]) if row[5] is not None else None,
            "max_s": float(row[6]) if row[6] is not None else None,
            "p90_s": float(row[7]) if row[7] is not None else None,
            "avg_speed_kmh": float(row[8]) if row[8] is not None else None,
        }
        for row in rows
        if row[2]
    ]
    slow = settings.max_urban_speed_kmh
    for route in routes:
        speed = route["avg_speed_kmh"]
        route["anomaly"] = bool(speed is not None and speed < slow * 0.25)
    return {"from": begin.isoformat(), "to": finish.isoformat(), "routes": routes}


@router.get("/dwell")
async def dwell(
    ch: ClickHouseDep,
    settings: SettingsDep,
    _user: UserDep,
    start: datetime | None = None,
    end: datetime | None = None,
    camera_id: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    limit = _resolved(limit, 500)
    begin, finish = _window(start, end)
    params: dict[str, Any] = {"start": _naive(begin), "end": _naive(finish), "limit": limit}
    where = "entry_ts >= {start:DateTime} AND entry_ts < {end:DateTime}"
    if camera_id:
        where += " AND camera_id = {camera:String}"
        params["camera"] = camera_id
    stored = _query(
        ch,
        f"""
        SELECT plate_norm, camera_id, vehicle_class, entry_ts, exit_ts, dwell_s, classification, confidence
        FROM dwell_events
        WHERE {where}
        ORDER BY entry_ts DESC
        LIMIT {{limit:UInt32}}
        """,
        params,
    )
    if stored:
        sessions = [
            {
                "plate": row[0],
                "camera_id": row[1],
                "vehicle_class": row[2],
                "entry_ts": str(row[3]),
                "exit_ts": str(row[4]),
                "dwell_s": float(row[5]),
                "classification": row[6],
                "confidence": float(row[7] or 0),
            }
            for row in stored
        ]
        return {
            "from": begin.isoformat(),
            "to": finish.isoformat(),
            "source": "dwell_events",
            "sessions": sessions,
        }
    read_where = "ts >= {start:DateTime} AND ts < {end:DateTime}"
    if camera_id:
        read_where += " AND camera_id = {camera:String}"
    raw = _query(
        ch,
        f"""
        SELECT plate_norm, camera_id, vehicle_class, ts, confidence
        FROM anpr_reads
        WHERE {read_where}
        ORDER BY ts
        LIMIT 20000
        """,
        params,
    )
    sightings = [
        {
            "plate": row[0],
            "camera_id": row[1],
            "vehicle_class": row[2],
            "ts": row[3].replace(tzinfo=UTC) if row[3].tzinfo is None else row[3],
            "confidence": float(row[4] or 0),
        }
        for row in raw
    ]
    thresholds = DwellThresholds(
        short_s=settings.stopped_short_s,
        excessive_s=settings.stopped_excessive_s,
        incident_s=settings.stopped_incident_s,
        gap_s=settings.stopped_gap_s,
    )
    sessions = sessionize_dwell(sightings, thresholds)[:limit]
    for session_row in sessions:
        session_row["entry_ts"] = session_row["entry_ts"].isoformat()
        session_row["exit_ts"] = session_row["exit_ts"].isoformat()
    return {
        "from": begin.isoformat(),
        "to": finish.isoformat(),
        "source": "anpr_reads",
        "sessions": sessions,
    }


@router.get("/vehicles")
async def vehicle_classes(
    ch: ClickHouseDep,
    _user: UserDep,
    start: datetime | None = None,
    end: datetime | None = None,
    camera_id: str | None = None,
) -> dict[str, Any]:
    begin, finish = _window(start, end)
    params: dict[str, Any] = {"start": _naive(begin), "end": _naive(finish)}
    where = "ts >= {start:DateTime} AND ts < {end:DateTime}"
    if camera_id:
        where += " AND camera_id = {camera:String}"
        params["camera"] = camera_id
    rows = _query(
        ch,
        f"""
        SELECT vehicle_class, camera_id, count()
        FROM anpr_reads
        WHERE {where}
        GROUP BY vehicle_class, camera_id
        """,
        params,
    )
    # Rows are already grouped; aggregate the counts instead of expanding one dict per read.
    summary = aggregate_vehicle_class_counts(rows)
    trend = _query(
        ch,
        f"""
        SELECT toStartOfHour(ts) AS hour, vehicle_class, count()
        FROM anpr_reads
        WHERE {where}
        GROUP BY hour, vehicle_class
        ORDER BY hour
        """,
        params,
    )
    return {
        "from": begin.isoformat(),
        "to": finish.isoformat(),
        **summary,
        "trend": [
            {"hour": str(row[0]), "vehicle_class": row[1], "count": int(row[2])} for row in trend
        ],
    }


@router.get("/reads")
async def recent_reads(
    ch: ClickHouseDep,
    _user: UserDep,
    camera_id: str | None = None,
    limit: int = Query(40, ge=1, le=200),
    source: str = Query("sim"),
) -> dict[str, Any]:
    limit = _resolved(limit, 40)
    source = _resolved(source, "sim")
    end = datetime.now(UTC)
    start = end - timedelta(minutes=15)
    params: dict[str, Any] = {"start": _naive(start), "limit": limit}
    where = "ts >= {start:DateTime}"
    if camera_id:
        where += " AND camera_id = {camera:String}"
        params["camera"] = camera_id
    elif normalize_source(source) == "video":
        where += " AND camera_id LIKE 'vid-%'"
    else:
        where += " AND camera_id NOT LIKE 'vid-%'"
    try:
        rows = _query(
            ch,
            f"""
            SELECT camera_id, ts, plate_norm, confidence, vehicle_class, track_id, bbox, crop_key, lane, direction
            FROM anpr_reads
            WHERE {where}
            ORDER BY ts DESC
            LIMIT {{limit:UInt32}}
            """,
            params,
        )
        extended = True
    except HTTPException:
        rows = _query(
            ch,
            f"""
            SELECT camera_id, ts, plate_norm, confidence, vehicle_class, crop_key, lane, direction
            FROM anpr_reads
            WHERE {where}
            ORDER BY ts DESC
            LIMIT {{limit:UInt32}}
            """,
            params,
        )
        extended = False
    reads = []
    for row in rows:
        item = {
            "camera_id": row[0],
            "ts": str(row[1]),
            "plate_norm": row[2],
            "confidence": float(row[3] or 0),
            "vehicle_class": row[4],
        }
        if extended:
            item.update(
                {
                    "track_id": row[5],
                    "bbox": list(row[6] or []),
                    "crop_key": row[7],
                    "lane": row[8],
                    "direction": row[9],
                }
            )
        else:
            item.update(
                {
                    "track_id": None,
                    "bbox": [],
                    "crop_key": row[5],
                    "lane": row[6],
                    "direction": row[7],
                }
            )
        reads.append(item)
    return {"reads": reads}


@router.get("/investigation")
async def investigation(
    ch: ClickHouseDep,
    session: SessionDep,
    user: OperatorUserDep,
    plate: str = Query(..., min_length=4),
    camera_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    limit = _resolved(limit, 500)
    if not role_can(user.role.value, "investigate"):
        raise HTTPException(status_code=403, detail="Not permitted")
    begin, finish = _window(start, end)
    plate_norm = plate.upper().replace(" ", "")
    params: dict[str, Any] = {
        "plate": plate_norm,
        "start": _naive(begin),
        "end": _naive(finish),
        "limit": limit,
    }
    where = "plate_norm = {plate:String} AND ts >= {start:DateTime} AND ts < {end:DateTime}"
    if camera_id:
        where += " AND camera_id = {camera:String}"
        params["camera"] = camera_id
    rows = _query(
        ch,
        f"""
        SELECT camera_id, ts, plate_norm, confidence, vehicle_class, crop_key, direction, speed_kmh
        FROM anpr_reads
        WHERE {where}
        ORDER BY ts
        LIMIT {{limit:UInt32}}
        """,
        params,
    )
    sightings = [
        {
            "camera_id": row[0],
            "ts": str(row[1]),
            "plate_norm": row[2],
            "confidence": float(row[3] or 0),
            "vehicle_class": row[4],
            "crop_key": row[5],
            "direction": row[6],
            "speed_kmh": float(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]
    alerts = await session.execute(
        select(AlertRow)
        .where(AlertRow.plate_norm == plate_norm)
        .order_by(AlertRow.created_at.desc())
        .limit(50)
    )
    session.add(
        audit_row(
            user,
            "investigation_query",
            plate_norm=plate_norm,
            params={"from": begin.isoformat(), "to": finish.isoformat()},
        )
    )
    await session.commit()
    return {
        "plate_norm": plate_norm,
        "from": begin.isoformat(),
        "to": finish.isoformat(),
        "sightings": sightings,
        "alerts": [
            {
                "id": str(row.id),
                "type": row.type,
                "status": row.status,
                "severity": row.severity,
                "created_at": row.created_at.isoformat(),
                "camera_ids": list(row.camera_ids or []),
            }
            for row in alerts.scalars()
        ],
    }


@router.get("/overview")
async def overview(
    ch: ClickHouseDep,
    session: SessionDep,
    settings: SettingsDep,
    _user: UserDep,
) -> dict[str, Any]:
    cameras = await _cameras(session)
    open_alerts = await session.execute(
        select(AlertRow).where(
            AlertRow.status.in_(["new", "reviewing", "acknowledged", "dispatched"])
        )
    )
    alert_rows = list(open_alerts.scalars())
    health_error = None
    summary = {"HEALTHY": 0, "DEGRADED": 0, "STALE": 0, "OFFLINE": 0}
    reads_15m = None
    try:
        finish = datetime.now(UTC)
        begin = finish - timedelta(seconds=settings.health_window_s)
        last, counts = _read_stats(ch, begin, finish)
        health_rows = _health_for(cameras, last, counts, finish, settings)
        for row in health_rows:
            summary[row["state"]] = summary.get(row["state"], 0) + 1
        count_rows = _query(
            ch,
            "SELECT count() FROM anpr_reads WHERE ts >= {start:DateTime}",
            {"start": _naive(finish - timedelta(minutes=15))},
        )
        reads_15m = int(count_rows[0][0]) if count_rows else 0
    except HTTPException as exc:
        health_error = exc.detail
    return {
        "active_cameras": sum(1 for cam in cameras if cam["status"] == "active"),
        "cameras": len(cameras),
        "open_alerts": len(alert_rows),
        "alert_types": _count(alert_rows),
        "reads_15m": reads_15m,
        "camera_health": summary,
        "analytics_error": health_error,
    }


def _count(alerts: list[AlertRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in alerts:
        counts[row.type] = counts.get(row.type, 0) + 1
    return counts
