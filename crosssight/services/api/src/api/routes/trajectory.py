"""Trajectory reconstruction endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from anpr_common.fuzzy import candidates
from anpr_common.grammar import normalize_plate
from fastapi import APIRouter, HTTPException, Query, status
from geoalchemy2.functions import ST_X, ST_Y
from sqlalchemy import select

from api.ch import ch_query
from api.db import AuditLogRow, Camera, CameraPair
from api.deps import ClickHouseDep, OperatorUserDep, SessionDep, SettingsDep
from api.schemas import GeoJSONFeatureCollection, TrajectorySummary
from api.trajectory_logic import (
    CameraPairInfo,
    Sighting,
    build_trajectory_legs,
    legs_to_geojson_features,
    merge_fuzzy_reads,
    trajectory_summary,
)

router = APIRouter(tags=["trajectory"])


@router.get("/trajectory", response_model=GeoJSONFeatureCollection)
async def get_trajectory(
    _user: OperatorUserDep,
    session: SessionDep,
    ch: ClickHouseDep,
    settings: SettingsDep,
    plate: str = Query(...),
    case_id: str = Query(...),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    fuzzy: bool = Query(False),
) -> GeoJSONFeatureCollection:
    if not case_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="case_id is required")

    plate_norm = normalize_plate(plate).norm or plate.upper().replace(" ", "")
    now = datetime.now(UTC)
    # Default to last 7 days so backfill / 60× sim data is visible without manual range.
    start = from_ts or (now - timedelta(days=7))
    end = to_ts or (now + timedelta(days=1))

    await _write_audit(session, _user.id, plate_norm, case_id, start, end, fuzzy)

    reads = await _fetch_reads(ch, plate_norm, start, end)
    data_status = "narrow"
    # If still empty, expand to all available history for this plate.
    if not reads and from_ts is None and to_ts is None:
        wide_start = datetime(2000, 1, 1, tzinfo=UTC)
        wide_end = now + timedelta(days=2)
        reads = await _fetch_reads(ch, plate_norm, wide_start, wide_end)
        data_status = "widened_to_all_history"
        await _write_audit(
            session,
            _user.id,
            plate_norm,
            case_id,
            wide_start,
            wide_end,
            fuzzy,
            action="trajectory_query_widened",
            reason="empty_result_fallback",
        )
    if fuzzy:
        pool = await _distinct_plates_in_window(ch, start, end)
        fuzzy_plates = [
            p for p, _ in candidates(plate_norm, pool=pool, max_cost=1.0) if p != plate_norm
        ]
        candidate_reads: dict[str, list[Sighting]] = {}
        for fp in fuzzy_plates:
            candidate_reads[fp] = await _fetch_reads(ch, fp, start, end)
        sightings = merge_fuzzy_reads(reads, candidate_reads)
    else:
        sightings = reads

    if not sightings:
        summary = _summary(plate_norm, [], [], {}, data_status)
        return GeoJSONFeatureCollection(features=[], summary=summary)

    pairs, coords = await _load_camera_graph(session)
    for s in sightings:
        if s.camera_id in coords:
            s.lat, s.lng = coords[s.camera_id]

    travel_times = await _fetch_travel_times(ch, start)
    legs = build_trajectory_legs(
        sightings,
        pairs,
        coords,
        max_speed_kmh=settings.max_urban_speed_kmh,
        travel_times=travel_times,
    )
    features = legs_to_geojson_features(sightings, legs)
    summary = _summary(plate_norm, sightings, legs, pairs, data_status)
    return GeoJSONFeatureCollection(features=features, summary=summary)


def _summary(plate_norm, sightings, legs, pairs, data_status: str) -> TrajectorySummary:
    payload = trajectory_summary(plate_norm, sightings, legs, pairs)
    payload["data_status"] = data_status
    return TrajectorySummary(**payload)


async def _write_audit(
    session,
    user_id: str,
    plate_norm: str,
    case_id: str,
    start: datetime,
    end: datetime,
    fuzzy: bool,
    *,
    action: str = "trajectory_query",
    reason: str | None = None,
) -> None:
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "fuzzy": fuzzy,
    }
    if reason is not None:
        params["reason"] = reason
    session.add(
        AuditLogRow(
            user_id=UUID(user_id) if user_id else None,
            action=action,
            plate_norm=plate_norm,
            case_id=case_id,
            params=params,
        )
    )
    await session.commit()


async def _fetch_reads(ch, plate_norm: str, start: datetime, end: datetime) -> list[Sighting]:
    query = """
        SELECT
            camera_id, ts, plate_norm, confidence, vehicle_class, color, direction, crop_key
        FROM anpr_reads
        WHERE plate_norm = {plate:String}
          AND ts >= {start:DateTime64(3)}
          AND ts <= {end:DateTime64(3)}
        ORDER BY ts
    """
    result = await ch_query(
        ch,
        query,
        parameters={
            "plate": plate_norm,
            "start": start,
            "end": end,
        },
    )
    return [
        Sighting(
            camera_id=row[0],
            ts=row[1].replace(tzinfo=UTC) if row[1].tzinfo is None else row[1],
            plate_norm=row[2],
            confidence=float(row[3]),
            vehicle_class=row[4],
            color=row[5],
            direction=row[6],
            crop_key=row[7],
        )
        for row in result.result_rows
    ]


async def _distinct_plates_in_window(ch, start: datetime, end: datetime) -> list[str]:
    query = """
        SELECT DISTINCT plate_norm
        FROM anpr_reads
        WHERE ts >= {start:DateTime64(3)} AND ts <= {end:DateTime64(3)}
        LIMIT 50000
    """
    result = await ch_query(ch, query, parameters={"start": start, "end": end})
    return [row[0] for row in result.result_rows]


async def _fetch_travel_times(ch, at: datetime) -> dict[tuple[str, str], float]:
    hour_start = at.replace(minute=0, second=0, microsecond=0)
    query = """
        SELECT camera_a, camera_b, median_travel_s
        FROM segment_speed_5min
        WHERE window_start >= {start:DateTime} - INTERVAL 1 HOUR
          AND window_start <= {start:DateTime} + INTERVAL 1 HOUR
          AND sample_count > 0
    """
    try:
        result = await ch_query(ch, query, parameters={"start": hour_start})
    except Exception:  # noqa: BLE001
        return {}
    return {(row[0], row[1]): float(row[2]) for row in result.result_rows}


async def _load_camera_graph(
    session,
) -> tuple[dict[tuple[str, str], CameraPairInfo], dict[str, tuple[float, float]]]:
    pair_result = await session.execute(select(CameraPair))
    pairs: dict[tuple[str, str], CameraPairInfo] = {}
    for p in pair_result.scalars():
        info = CameraPairInfo(
            camera_a=p.camera_a,
            camera_b=p.camera_b,
            distance_m=p.distance_m,
            adjacent=p.adjacent,
        )
        pairs[(p.camera_a, p.camera_b)] = info

    cam_result = await session.execute(select(Camera.id, ST_Y(Camera.geom), ST_X(Camera.geom)))
    coords: dict[str, tuple[float, float]] = {}
    for cam_id, lat, lng in cam_result.all():
        coords[cam_id] = (float(lat), float(lng))

    return pairs, coords
