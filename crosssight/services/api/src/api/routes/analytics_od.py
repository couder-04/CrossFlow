"""Origin-destination analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from anpr_common.geo import h3_to_str
from fastapi import APIRouter, HTTPException, Query, status

from api.deps import ClickHouseDep, SettingsDep, UserDep

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/od")
async def od_matrix(
    ch: ClickHouseDep,
    settings: SettingsDep,
    _user: UserDep,
    hour: int = Query(..., ge=0, le=23),
    date: str = Query(..., description="YYYY-MM-DD"),
) -> dict[str, Any]:
    try:
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date") from exc
    hour_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
    hour_naive = hour_dt.replace(tzinfo=None)
    query = """
        SELECT origin_h3, dest_h3, sum(trip_count) AS trips
        FROM od_hourly
        WHERE toStartOfHour(hour) = {hour:DateTime}
        GROUP BY origin_h3, dest_h3
        HAVING trips >= {k:UInt32}
    """
    result = ch.query(query, parameters={"hour": hour_naive, "k": settings.od_k_anon})
    cells = [
        {
            "origin_h3": h3_to_str(int(row[0])),
            "dest_h3": h3_to_str(int(row[1])),
            "trip_count": int(row[2]),
        }
        for row in result.result_rows
    ]
    # Fallback: derive OD from raw reads when od_hourly is empty.
    if not cells:
        fallback = """
            SELECT origin, dest, count() AS trips
            FROM (
                SELECT
                    plate_norm,
                    argMin(h3_r7, ts) AS origin,
                    argMax(h3_r7, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime}
                  AND ts < {end:DateTime}
                  AND h3_r7 != 0
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            HAVING trips >= {k:UInt32}
        """
        end = hour_naive + timedelta(hours=1)
        result = ch.query(
            fallback,
            parameters={"start": hour_naive, "end": end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    # Day-wide fallback for sparse synthetic demos (still k-anonymous).
    if not cells:
        day_start = hour_naive.replace(hour=0)
        day_end = day_start + timedelta(days=1)
        day_q = """
            SELECT origin_h3, dest_h3, sum(trip_count) AS trips
            FROM od_hourly
            WHERE hour >= {start:DateTime} AND hour < {end:DateTime}
            GROUP BY origin_h3, dest_h3
            HAVING trips >= {k:UInt32}
        """
        result = ch.query(
            day_q,
            parameters={"start": day_start, "end": day_end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    if not cells:
        day_start = hour_naive.replace(hour=0)
        day_end = day_start + timedelta(days=1)
        day_reads = """
            SELECT origin, dest, count() AS trips
            FROM (
                SELECT
                    plate_norm,
                    argMin(h3_r7, ts) AS origin,
                    argMax(h3_r7, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime}
                  AND ts < {end:DateTime}
                  AND h3_r7 != 0
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            HAVING trips >= {k:UInt32}
        """
        result = ch.query(
            day_reads,
            parameters={"start": day_start, "end": day_end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    return {
        "date": date,
        "hour": hour,
        "k_anonymity": settings.od_k_anon,
        "cells": cells,
    }
