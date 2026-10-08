"""Signal-control results (XtraFlow) and measured junction demand (CrossSight cameras).

The reading and the demand maths live in the ``crossflow`` package so the API, the CLI and
the tests share one implementation. When that package or the result files are absent the
endpoints answer ``available: false`` instead of failing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.ch import ch_query
from api.deps import ClickHouseDep, UserDep

try:  # the package is mounted/installed alongside the API; the API still boots without it
    from crossflow import signals as _signals
    from crossflow.bridge import APPROACHES, FlowError, approach_rates, vehicle_mix
except ImportError:  # pragma: no cover - exercised only in images without crossflow
    _signals = None

router = APIRouter(prefix="/signals", tags=["signals"])

MAX_PERIOD_HOURS = 24 * 7


def _require() -> Any:
    if _signals is None:
        raise HTTPException(status_code=503, detail="crossflow package is not installed")
    return _signals


@router.get("/summary")
async def summary(_user: UserDep) -> dict[str, Any]:
    """Published XtraFlow evaluation plus a digest of the latest pipeline run."""
    if _signals is None:
        return {"available": False, "reason": "crossflow package is not installed"}
    pub = _signals.published_summary()
    run = _signals.latest_run()
    return {
        **pub,
        "latest_run": None
        if run is None
        else {
            "created": run.get("created"),
            "mode": (run.get("run") or {}).get("mode"),
            "verdict": (run.get("comparison") or {}).get("verdict"),
        },
    }


@router.get("/runs/latest")
async def latest_run(_user: UserDep) -> dict[str, Any]:
    """The full report of the newest ``crossflow run``."""
    sig = _require()
    run = sig.latest_run()
    if run is None:
        return {"available": False, "reason": "no pipeline run yet; run `crossflow run`"}
    run = {k: v for k, v in run.items() if k != "raw_runs"}
    return {"available": True, "report": run}


def _parse_cameras(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        cam, sep, arm = part.rpartition(":")
        arm = arm.strip().upper()
        if not sep or not cam.strip() or arm not in ("N", "S", "E", "W"):
            raise HTTPException(
                status_code=422,
                detail=f"bad camera spec {part!r}; use camera_id:ARM with ARM in N,S,E,W",
            )
        out[cam.strip()] = arm
    if not out:
        raise HTTPException(status_code=422, detail="cameras must name at least one camera:ARM")
    return out


@router.get("/demand")
async def demand(
    ch: ClickHouseDep,
    _user: UserDep,
    cameras: str = Query(..., description="camera_id:ARM pairs, e.g. cam-001:N,cam-002:S"),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
) -> dict[str, Any]:
    """Approach demand (veh/h) measured by cameras over a period, ready for signal control.

    ``flow_5min`` has no row for a window with no traffic, so the rate divides by the length
    of the period you ask for, not by the number of rows.
    """
    sig = _require()
    cam_map = _parse_cameras(cameras)
    hours = (to_ts - from_ts).total_seconds() / 3600.0
    if hours <= 0 or hours > MAX_PERIOD_HOURS:
        raise HTTPException(status_code=422, detail="period must be positive and at most 7 days")

    result = await ch_query(
        ch,
        """
        SELECT camera_id,
               sum(counts_car), sum(counts_motorcycle), sum(counts_bus),
               sum(counts_truck), sum(counts_auto), sum(counts_other), sum(volume)
        FROM flow_5min
        WHERE camera_id IN {ids:Array(String)}
          AND window_start >= {start:DateTime}
          AND window_start < {end:DateTime}
        GROUP BY camera_id
        """,
        parameters={"ids": list(cam_map), "start": from_ts, "end": to_ts},
    )
    seen: dict[str, dict[str, Any]] = {}
    for row in result.result_rows:
        seen[str(row[0])] = {
            "counts_by_class": {
                "car": int(row[1] or 0),
                "motorcycle": int(row[2] or 0),
                "bus": int(row[3] or 0),
                "truck": int(row[4] or 0),
                "auto": int(row[5] or 0),
                "other": int(row[6] or 0),
            },
            "volume": int(row[7] or 0),
        }

    # A camera with no rows saw nothing in the period: that is a zero, not missing data.
    records = [
        {
            "camera_id": cam,
            "approach": arm,
            "window_start": from_ts.isoformat(),
            "window_end": to_ts.isoformat(),
            **seen.get(cam, {"counts_by_class": {}, "volume": 0}),
        }
        for cam, arm in cam_map.items()
    ]
    try:
        rates = approach_rates(records, period_hours=hours)
        try:
            mix = vehicle_mix(records)
        except FlowError:
            mix = None  # nothing classified in the period
    except FlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    base = sig.base_demand()
    return {
        "from": from_ts.isoformat(),
        "to": to_ts.isoformat(),
        "period_hours": round(hours, 4),
        "cameras": cam_map,
        "silent_cameras": sorted(c for c in cam_map if c not in seen),
        "approach_veh_h": rates,
        "scale_vs_xtraflow_balanced": {
            a: round(rates[a] / base[a], 3) for a in APPROACHES if a in rates and base.get(a)
        },
        "vehicle_mix": mix,
    }
