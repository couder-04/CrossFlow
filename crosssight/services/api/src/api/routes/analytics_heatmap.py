"""Heatmap analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from anpr_common.geo import h3_to_str
from fastapi import APIRouter, HTTPException, Query, status

from api.ch import ch_query
from api.deps import ClickHouseDep, UserDep
from api.video_feeds import normalize_source

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _parse_window(window: str) -> timedelta:
    window = window.strip().lower()
    if window.endswith("m"):
        return timedelta(minutes=int(window[:-1]))
    if window.endswith("h"):
        return timedelta(hours=int(window[:-1]))
    if window.endswith("d"):
        return timedelta(days=int(window[:-1]))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid window format")


async def _video_heatmap_cells(ch, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Plate reads that came from the video cameras only."""
    result = await ch_query(
        ch,
        """
        SELECT h3_r8, count() AS total
        FROM anpr_reads
        WHERE ts >= {start:DateTime} AND ts <= {end:DateTime}
          AND h3_r8 != 0
          AND camera_id LIKE 'vid-%'
        GROUP BY h3_r8
        ORDER BY total DESC
        """,
        parameters={
            "start": start.replace(tzinfo=None) if start.tzinfo else start,
            "end": end.replace(tzinfo=None) if end.tzinfo else end,
        },
    )
    return [
        {"h3": h3_to_str(int(row[0])), "h3_int": int(row[0]), "count": int(row[1])}
        for row in result.result_rows
    ]


async def _heatmap_cells(ch, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Prefer heatmap_1min; fall back to aggregating anpr_reads."""
    query = """
        SELECT h3_cell, sum(count) AS total
        FROM heatmap_1min
        WHERE minute >= {start:DateTime} AND minute <= {end:DateTime}
        GROUP BY h3_cell
        ORDER BY total DESC
    """
    result = await ch_query(
        ch,
        query,
        parameters={
            "start": start.replace(tzinfo=None) if start.tzinfo else start,
            "end": end.replace(tzinfo=None) if end.tzinfo else end,
        },
    )
    cells = [
        {"h3": h3_to_str(int(row[0])), "h3_int": int(row[0]), "count": int(row[1])}
        for row in result.result_rows
    ]
    if cells:
        return cells
    fallback = """
        SELECT h3_r8, count() AS total
        FROM anpr_reads
        WHERE ts >= {start:DateTime} AND ts <= {end:DateTime} AND h3_r8 != 0
        GROUP BY h3_r8
        ORDER BY total DESC
    """
    result = await ch_query(
        ch,
        fallback,
        parameters={
            "start": start.replace(tzinfo=None) if start.tzinfo else start,
            "end": end.replace(tzinfo=None) if end.tzinfo else end,
        },
    )
    return [
        {"h3": h3_to_str(int(row[0])), "h3_int": int(row[0]), "count": int(row[1])}
        for row in result.result_rows
    ]


@router.get("/heatmap")
async def heatmap(
    ch: ClickHouseDep,
    _user: UserDep,
    window: str = Query("15m"),
    source: str = Query("sim"),
) -> dict[str, Any]:
    delta = _parse_window(window)
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    start = end - delta
    if normalize_source(source) == "video":
        cells = await _video_heatmap_cells(ch, start, end)
        return {
            "window": window,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cells": cells,
            "stale": False,
            "latest_ts": None,
        }
    cells = await _heatmap_cells(ch, start, end)
    stale = False
    latest_ts: str | None = None
    # If wall-clock window is empty (common under 60× sim), use latest data window.
    if not cells:
        latest = await ch_query(ch, "SELECT max(ts) FROM anpr_reads")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            end_raw = latest.result_rows[0][0]
            if hasattr(end_raw, "tzinfo") and end_raw.tzinfo is None:
                end_raw = end_raw.replace(tzinfo=UTC)
            latest_ts = end_raw.isoformat() if hasattr(end_raw, "isoformat") else str(end_raw)
            end = (
                end_raw.replace(second=0, microsecond=0) if hasattr(end_raw, "replace") else end_raw
            )
            start = end - delta
            cells = await _heatmap_cells(ch, start, end)
            stale = True
    return {
        "window": window,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cells": cells,
        "stale": stale,
        "latest_ts": latest_ts,
    }
