"""Flow, segment, bottleneck, anomaly, and route-density analytics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from api.ch import ch_query
from api.deps import ClickHouseDep, UserDep
from api.routes.analytics_heatmap import _parse_window

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/flow")
async def flow(
    ch: ClickHouseDep,
    _user: UserDep,
    camera_id: str = Query(...),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
) -> dict[str, Any]:
    query = """
        SELECT
            window_start,
            lane,
            counts_car, counts_motorcycle, counts_bus, counts_truck, counts_auto, counts_other,
            avg_speed_kmh,
            volume
        FROM flow_5min
        WHERE camera_id = {camera_id:String}
          AND window_start >= {start:DateTime}
          AND window_start <= {end:DateTime}
        ORDER BY window_start, lane
    """
    result = await ch_query(
        ch,
        query,
        parameters={"camera_id": camera_id, "start": from_ts, "end": to_ts},
    )
    rows = []
    for row in result.result_rows:
        rows.append(
            {
                "window_start": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
                "lane": row[1],
                "counts_by_class": {
                    "car": int(row[2]),
                    "motorcycle": int(row[3]),
                    "bus": int(row[4]),
                    "truck": int(row[5]),
                    "auto": int(row[6]),
                    "other": int(row[7]),
                },
                "avg_speed_kmh": float(row[8]) if row[8] is not None else None,
                "volume": int(row[9]),
            }
        )
    return {
        "camera_id": camera_id,
        "from": from_ts.isoformat(),
        "to": to_ts.isoformat(),
        "windows": rows,
    }


@router.get("/segments")
async def segments(
    ch: ClickHouseDep,
    _user: UserDep,
    at: datetime = Query(...),
) -> dict[str, Any]:
    """Congestion index per directed camera pair at the given time."""
    segments_out = await _segment_congestion(ch, at)
    if not segments_out:
        latest = await ch_query(ch, "SELECT max(window_start) FROM segment_speed_5min")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            at = latest.result_rows[0][0]
            if hasattr(at, "tzinfo") and at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            segments_out = await _segment_congestion(ch, at)
    return {"at": at.isoformat() if hasattr(at, "isoformat") else str(at), "segments": segments_out}


async def _segment_congestion(ch, at: datetime) -> list[dict[str, Any]]:
    at_hour = at.replace(minute=0, second=0, microsecond=0)
    query = """
        SELECT
            camera_a,
            camera_b,
            median(median_speed_kmh) AS speed_kmh,
            median(median_travel_s) AS travel_s,
            sum(sample_count) AS samples
        FROM segment_speed_5min
        WHERE window_start >= {start:DateTime} - INTERVAL 1 HOUR
          AND window_start <= {start:DateTime} + INTERVAL 1 HOUR
        GROUP BY camera_a, camera_b
        HAVING samples > 0
    """
    current = await ch_query(ch, query, parameters={"start": at_hour})
    baseline_query = """
        SELECT camera_a, camera_b, quantile(0.85)(median_speed_kmh) AS free_flow
        FROM segment_speed_5min
        WHERE toHour(window_start) BETWEEN 0 AND 4
        GROUP BY camera_a, camera_b
    """
    try:
        baseline = await ch_query(ch, baseline_query)
        free_flow = {(r[0], r[1]): float(r[2]) for r in baseline.result_rows if r[2]}
    except Exception:  # noqa: BLE001
        free_flow = {}

    segments_out = []
    for row in current.result_rows:
        key = (row[0], row[1])
        speed = float(row[2]) if row[2] else 0.0
        ff = free_flow.get(key, speed if speed > 0 else 1.0)
        congestion = 1.0 - (speed / ff) if ff > 0 else 0.0
        segments_out.append(
            {
                "camera_a": row[0],
                "camera_b": row[1],
                "median_speed_kmh": speed,
                "median_travel_s": float(row[3]) if row[3] else None,
                "sample_count": int(row[4]),
                "free_flow_speed_kmh": ff,
                "congestion_index": max(0.0, min(1.0, congestion)),
            }
        )
    return segments_out


@router.get("/bottlenecks")
async def bottlenecks(ch: ClickHouseDep, _user: UserDep) -> dict[str, Any]:
    at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    seg_data = await _segment_congestion(ch, at)
    flow_baselines = await _camera_flow_baselines(ch)
    out = []
    for seg in seg_data:
        if seg["congestion_index"] < 0.5:
            continue
        downstream = seg["camera_b"]
        baseline = flow_baselines.get(downstream, 0)
        current = await _current_camera_volume(ch, downstream, at)
        if baseline <= 0:
            continue
        if current >= 0.6 * baseline:
            continue
        out.append(
            {
                **seg,
                "downstream_camera": downstream,
                "current_volume": current,
                "baseline_volume": baseline,
            }
        )
    return {"at": at.isoformat(), "bottlenecks": out}


@router.get("/anomalies")
async def anomalies(ch: ClickHouseDep, _user: UserDep) -> dict[str, Any]:
    at = datetime.now(UTC)
    query = """
        SELECT camera_id, sum(volume) AS vol
        FROM flow_5min
        WHERE window_start >= {start:DateTime} - INTERVAL 15 MINUTE
        GROUP BY camera_id
    """
    current_result = await ch_query(ch, query, parameters={"start": at})
    current = {row[0]: int(row[1]) for row in current_result.result_rows}
    baseline_query = """
        SELECT
            camera_id,
            toDayOfWeek(window_start) AS dow,
            toHour(window_start) AS hr,
            avg(volume) AS avg_vol,
            stddevPop(volume) AS std_vol
        FROM flow_5min
        GROUP BY camera_id, dow, hr
    """
    baselines: dict[str, tuple[float, float]] = {}
    dow = at.isoweekday()
    hr = at.hour
    for row in (await ch_query(ch, baseline_query)).result_rows:
        if int(row[1]) == dow and int(row[2]) == hr:
            baselines[row[0]] = (float(row[3] or 0), float(row[4] or 1.0))

    flagged = []
    for cam_id, vol in current.items():
        avg_vol, std_vol = baselines.get(cam_id, (0.0, 1.0))
        if std_vol <= 0:
            continue
        z = (vol - avg_vol) / std_vol
        if z > 3:
            flagged.append(
                {
                    "camera_id": cam_id,
                    "volume": vol,
                    "baseline_avg": avg_vol,
                    "z_score": round(z, 3),
                }
            )
    flagged.sort(key=lambda x: x["z_score"], reverse=True)
    return {"at": at.isoformat(), "anomalies": flagged}


@router.get("/route-density")
async def route_density(
    ch: ClickHouseDep,
    _user: UserDep,
    window: str = Query("1h"),
) -> dict[str, Any]:
    """Aggregate consecutive camera-pair hop counts (route corridor density)."""
    delta = _parse_window(window)
    end = datetime.now(UTC)
    start = end - delta
    start_n = start.replace(tzinfo=None)
    end_n = end.replace(tzinfo=None)
    query = """
        SELECT camera_a, camera_b, count() AS hops
        FROM (
            SELECT
                plate_norm,
                camera_id AS camera_b,
                lagInFrame(camera_id) OVER (
                    PARTITION BY plate_norm ORDER BY ts
                ) AS camera_a,
                ts
            FROM anpr_reads
            WHERE ts >= {start:DateTime} AND ts <= {end:DateTime}
        )
        WHERE camera_a != '' AND camera_a IS NOT NULL AND camera_a != camera_b
        GROUP BY camera_a, camera_b
        ORDER BY hops DESC
        LIMIT 500
    """
    try:
        result = await ch_query(ch, query, parameters={"start": start_n, "end": end_n})
        corridors = [
            {"camera_a": row[0], "camera_b": row[1], "hop_count": int(row[2])}
            for row in result.result_rows
        ]
    except Exception:  # noqa: BLE001
        # Fallback without window functions: use segment_speed sample counts.
        fallback = """
            SELECT camera_a, camera_b, sum(sample_count) AS hops
            FROM segment_speed_5min
            WHERE window_start >= {start:DateTime} AND window_start <= {end:DateTime}
            GROUP BY camera_a, camera_b
            ORDER BY hops DESC
            LIMIT 500
        """
        result = await ch_query(ch, fallback, parameters={"start": start_n, "end": end_n})
        corridors = [
            {"camera_a": row[0], "camera_b": row[1], "hop_count": int(row[2])}
            for row in result.result_rows
        ]
    if not corridors:
        # Use latest available window when wall-clock is empty (60× sim).
        latest = await ch_query(ch, "SELECT max(ts) FROM anpr_reads")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            end_n = latest.result_rows[0][0]
            if hasattr(end_n, "tzinfo") and end_n.tzinfo is not None:
                end_n = end_n.replace(tzinfo=None)
            start_n = end_n - delta
            try:
                result = await ch_query(ch, query, parameters={"start": start_n, "end": end_n})
                corridors = [
                    {"camera_a": row[0], "camera_b": row[1], "hop_count": int(row[2])}
                    for row in result.result_rows
                ]
            except Exception:  # noqa: BLE001
                result = await ch_query(
                    ch,
                    """
                    SELECT camera_a, camera_b, sum(sample_count) AS hops
                    FROM segment_speed_5min
                    WHERE window_start >= {start:DateTime} AND window_start <= {end:DateTime}
                    GROUP BY camera_a, camera_b
                    ORDER BY hops DESC
                    LIMIT 500
                    """,
                    parameters={"start": start_n, "end": end_n},
                )
                corridors = [
                    {"camera_a": row[0], "camera_b": row[1], "hop_count": int(row[2])}
                    for row in result.result_rows
                ]
    return {
        "window": window,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "corridors": corridors,
    }


async def _camera_flow_baselines(ch) -> dict[str, float]:
    query = """
        SELECT camera_id, avg(volume) AS baseline
        FROM flow_5min
        WHERE toHour(window_start) BETWEEN 0 AND 5
        GROUP BY camera_id
    """
    try:
        return {row[0]: float(row[1]) for row in (await ch_query(ch, query)).result_rows}
    except Exception:  # noqa: BLE001
        return {}


async def _current_camera_volume(ch, camera_id: str, at: datetime) -> int:
    query = """
        SELECT sum(volume) FROM flow_5min
        WHERE camera_id = {cam:String}
          AND window_start >= {start:DateTime} - INTERVAL 15 MINUTE
    """
    result = await ch_query(ch, query, parameters={"cam": camera_id, "start": at})
    if not result.result_rows:
        return 0
    return int(result.result_rows[0][0] or 0)
