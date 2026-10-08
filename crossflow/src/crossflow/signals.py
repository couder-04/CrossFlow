"""Read XtraFlow's published results and CrossFlow pipeline reports.

Standard library only, so the CrossSight API (a slim Python image) can import it.
Nothing here runs a simulation or writes a file; it only reads.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .paths import runs_dir, xtraflow_dir, xtraflow_results

CONTROLLER_LABELS = {
    "fixed": "Fixed (untuned)",
    "fixed_tuned": "Fixed (tuned)",
    "webster": "Webster",
    "actuated": "Actuated",
    "queue_pressure": "Queue pressure",
    "maxpressure": "Max pressure",
    "ours_count": "XtraFlow (count only)",
    "XtraFlow": "XtraFlow (fuel-weighted)",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def published_summary() -> dict[str, Any]:
    """XtraFlow's locked evaluation: per scenario and controller, plus headline gains."""
    results = xtraflow_results()
    raw = results / "raw_runs.csv"
    if not raw.exists():
        return {
            "available": False,
            "reason": f"{raw} not found",
            "controllers": [],
            "headlines": [],
        }

    acc: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    n_rows = 0
    with raw.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") != "ok":
                continue
            n_rows += 1
            key = (row["scenario"], row["controller"])
            for col in (
                "fuel_per_vehicle_L",
                "mean_waiting_s",
                "mean_queue_veh",
                "stops_per_vehicle",
            ):
                v = _num(row.get(col))
                if v is not None:
                    acc[key][col].append(v)

    controllers = []
    for (scenario, controller), cols in sorted(acc.items()):
        n = len(cols["fuel_per_vehicle_L"])
        controllers.append(
            {
                "scenario": scenario,
                "controller": controller,
                "label": CONTROLLER_LABELS.get(controller, controller),
                "n_seeds": n,
                **{c: round(sum(v) / len(v), 4) for c, v in cols.items() if v},
            }
        )

    headlines = _read_json(results / "headlines.json") or []
    lock = _read_json(results / "config.lock")
    return {
        "available": True,
        "n_runs": n_rows,
        "controllers": controllers,
        "headlines": headlines if isinstance(headlines, list) else [],
        "config_locked": bool(lock),
        "label": "Simulation-based estimate; assumed traffic mix",
        "note": (
            "Gains are against the best baseline, not against the untuned fixed plan. "
            "Where the confidence interval spans zero the difference is not established."
        ),
    }


def list_runs() -> list[dict[str, Any]]:
    """Pipeline reports from ``crossflow run``, newest first (summary fields only)."""
    base = runs_dir()
    out = []
    if not base.is_dir():
        return out
    for rep in sorted(base.glob("*/report.json"), reverse=True):
        data = _read_json(rep)
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "run_id": rep.parent.name,
                "created": data.get("created"),
                "mode": (data.get("run") or {}).get("mode"),
                "verdict": (data.get("comparison") or {}).get("verdict"),
            }
        )
    return out


def latest_run() -> dict[str, Any] | None:
    """The newest full pipeline report, or None when ``crossflow run`` has never been used."""
    runs = list_runs()
    if not runs:
        return None
    data = _read_json(runs_dir() / runs[0]["run_id"] / "report.json")
    return data if isinstance(data, dict) else None


_ARMS = ("N", "S", "E", "W")


def _parse_base_demand(text: str) -> dict[str, float]:
    """Read ``demand: balanced: {N,S,E,W}`` from config text, stdlib only.

    Finds the ``balanced:`` block under the top-level ``demand:`` key and takes the four arm
    keys in any order, as ints or floats, ignoring comments. Returns {} unless all four are
    present.
    """
    import re

    out: dict[str, float] = {}
    in_demand = False
    balanced_indent: int | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if balanced_indent is not None:
            if indent <= balanced_indent:
                break
            m = re.fullmatch(r"\s*([NSEW])\s*:\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", line)
            if m:
                out[m.group(1)] = float(m.group(2))
        elif in_demand:
            if indent == 0:
                in_demand = False
            elif re.fullmatch(r"\s*balanced\s*:", line):
                balanced_indent = indent
        elif indent == 0 and re.fullmatch(r"demand\s*:", line):
            in_demand = True
    return out if set(out) == set(_ARMS) else {}


def _yaml_base_demand(text: str) -> dict[str, float]:
    """Fallback for layouts the line parser does not read (e.g. flow style ``{N: 1, ...}``)."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        block = yaml.safe_load(text)["demand"]["balanced"]
        out = {a: float(block[a]) for a in _ARMS}
    except (yaml.YAMLError, KeyError, TypeError, ValueError):
        return {}
    return out


def base_demand() -> dict[str, float]:
    """XtraFlow's balanced-scenario demand (veh/h per arm), the denominator of calibration."""
    cfg = xtraflow_dir() / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse_base_demand(text) or _yaml_base_demand(text)
