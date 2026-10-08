"""Turn CrossSight ``FlowWindow`` records into XtraFlow demand calibration data.

A record names the arm it measures in one of two ways:

* a camera map ``{camera_id: "N" | "S" | "E" | "W"}``: one camera watches one arm, or
* an explicit ``approach`` field on the record: one camera at the junction reports
  each arm separately (what the offline city simulation produces).

Arm convention follows XtraFlow: the arm is the side the traffic *arrives from*.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

APPROACHES = ("N", "S", "E", "W")

# CrossSight VehicleClass -> XtraFlow vehicle_mix key. "other" has no XtraFlow
# emission class, so it is dropped from the mix instead of being guessed.
CLASS_MAP = {
    "motorcycle": "two_wheeler",
    "auto": "auto_rickshaw",
    "car": "car",
    "bus": "bus",
    "truck": "truck",
}


class FlowError(ValueError):
    """Raised for malformed flow input or camera maps."""


def _parse_ts(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise FlowError(f"{field} must be an ISO-8601 string, got {value!r}")
    try:
        return datetime.fromisoformat(value)  # 3.11+ accepts a trailing "Z"
    except ValueError as exc:
        raise FlowError(f"{field} is not ISO-8601: {value!r}") from exc


def load_camera_map(path: str | Path) -> dict[str, str]:
    """Load ``{camera_id: approach}`` where approach is one of N, S, E, W.

    A camera may be omitted; its flow is then ignored.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise FlowError("camera map must be a non-empty JSON object")
    out: dict[str, str] = {}
    for cam, arm in data.items():
        arm_u = str(arm).upper()
        if arm_u not in APPROACHES:
            raise FlowError(f"camera {cam!r}: approach must be one of {APPROACHES}, got {arm!r}")
        out[str(cam)] = arm_u
    return out


def load_flow_windows(path: str | Path) -> list[dict[str, Any]]:
    """Read FlowWindow records from JSON Lines or a JSON array."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise FlowError(f"{path} is empty")
    if text.startswith("["):
        records = json.loads(text)
    else:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise FlowError(f"record {i} is not an object")
        for key in ("camera_id", "window_start", "window_end"):
            if key not in rec:
                raise FlowError(f"record {i} is missing {key!r}")
    return records


def _volume(rec: dict[str, Any]) -> int:
    counts = rec.get("counts_by_class") or {}
    by_class = sum(int(v) for v in counts.values())
    volume = int(rec.get("volume") or 0)
    total = volume if volume else by_class
    if total < 0 or any(int(v) < 0 for v in counts.values()):
        raise FlowError(f"negative count in record for camera {rec.get('camera_id')!r}")
    return total


def _arm(rec: dict[str, Any], camera_map: dict[str, str] | None) -> str | None:
    explicit = rec.get("approach")
    if explicit is not None:
        arm = str(explicit).upper()
        if arm not in APPROACHES:
            raise FlowError(f"approach must be one of {APPROACHES}, got {explicit!r}")
        return arm
    return (camera_map or {}).get(str(rec["camera_id"]))


def _dedupe(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact repeats (same camera, arm, window, lane) from at-least-once delivery."""
    seen: set[tuple[Any, ...]] = set()
    unique = []
    for rec in records:
        key = (
            rec["camera_id"],
            rec.get("approach"),
            rec["window_start"],
            rec["window_end"],
            rec.get("lane"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique


def approach_rates(
    records: Iterable[dict[str, Any]],
    camera_map: dict[str, str] | None = None,
    *,
    period_hours: float | None = None,
) -> dict[str, float]:
    """Mean veh/h per approach.

    Per source (camera, arm): vehicles over all lanes divided by the observed time. Sources
    on the same arm cover different lanes or sub-arms, so their rates add. Arms with no
    mapped data are omitted rather than reported as zero.

    By default the observed time is the sum of that source's distinct windows. That is only
    right when idle windows are present in the data. CrossSight's ``flow_5min`` table has no
    row for a window with no traffic, so callers reading it must pass ``period_hours`` (the
    length of the period they queried) or quiet arms would be over-reported.
    """
    if period_hours is not None and period_hours <= 0:
        raise FlowError("period_hours must be positive")
    records = _dedupe(records)
    vehicles: dict[tuple[str, str], int] = defaultdict(int)
    windows: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    window_s: dict[tuple[str, str, str, str], float] = {}
    for rec in records:
        arm = _arm(rec, camera_map)
        if arm is None:
            continue
        cam = str(rec["camera_id"])
        start = _parse_ts(rec["window_start"], "window_start")
        end = _parse_ts(rec["window_end"], "window_end")
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            raise FlowError(f"camera {cam!r}: window_end must be after window_start")
        src = (cam, arm)
        vehicles[src] += _volume(rec)
        key = (rec["window_start"], rec["window_end"])
        windows[src].add(key)
        window_s[(cam, arm, *key)] = seconds

    rates: dict[str, float] = defaultdict(float)
    for src, veh in vehicles.items():
        if period_hours is not None:
            hours = period_hours
        else:
            hours = sum(window_s[(*src, *k)] for k in windows[src]) / 3600.0
        rates[src[1]] += veh / hours
    return {a: round(rates[a], 1) for a in APPROACHES if a in rates}


def vehicle_mix(
    records: Iterable[dict[str, Any]], camera_map: dict[str, str] | None = None
) -> dict[str, float]:
    """Observed class shares in XtraFlow's ``vehicle_mix`` vocabulary (sums to 1)."""
    totals: dict[str, int] = defaultdict(int)
    for rec in _dedupe(records):
        # With a camera map, only mapped cameras count; without one, every record does.
        if camera_map is not None and _arm(rec, camera_map) is None:
            continue
        for cls, n in (rec.get("counts_by_class") or {}).items():
            target = CLASS_MAP.get(cls)
            if target:
                totals[target] += int(n)
    grand = sum(totals.values())
    if grand <= 0:
        raise FlowError("no classified vehicles to build a mix from")
    return {k: round(totals[k] / grand, 4) for k in CLASS_MAP.values()}


def write_observed_counts(rates: dict[str, float], out: str | Path) -> Path:
    """Write the ``approach,count_veh_h`` CSV that ``calibrate_demand`` reads."""
    if not rates:
        raise FlowError("no approach had any mapped flow data")
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["approach", "count_veh_h"])
        for arm in APPROACHES:
            if arm in rates:
                writer.writerow([arm, rates[arm]])
    return path
