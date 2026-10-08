"""Phase decisions + idle-fuel proxy from YOLO count series.

Maps detector counts → XtraFlow next-phase choices over time, and compares
fuel-weighted serving vs a fixed round-robin baseline using idle-fuel weights.

CCTV timelines are illustrative (input feasibility). Published % fuel reduction
still comes from the SUMO study in results/headlines.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perception.replay import replay_decision
from sim.controllers import PHASE_ORDER
from sim.util import ROOT, load_json

PHASE_APPROACHES = {
    "NS_TL": ("N", "S"),
    "NS_R": ("N", "S"),
    "EW_TL": ("E", "W"),
    "EW_R": ("E", "W"),
}
CLASS_KEYS = ("car", "two_wheeler", "bus", "truck", "auto_rickshaw")


def _weights() -> dict:
    path = ROOT / "results" / "weights.json"
    if path.exists():
        return load_json(path)
    return {
        "idle_fuel_mg_per_s": {"car": 837.0, "truck": 2321.0, "bus": 1671.0, "two_wheeler": 448.0, "auto_rickshaw": 837.0},
        "w_type": {"car": 1.0, "truck": 2.0, "bus": 2.5, "two_wheeler": 0.4, "auto_rickshaw": 0.7},
    }


def _empty_approach() -> dict[str, float]:
    return {k: 0 for k in CLASS_KEYS} | {"halting_est": 0, "weighted_pressure": 0.0}


def _approach_idle_mg_s(counts: dict[str, Any], idle: dict[str, float]) -> float:
    """Idle-fuel proxy (mg/s) for vehicles waiting on one approach."""
    if not isinstance(counts, dict):
        return 0.0
    total = 0.0
    # Prefer halting vehicles; if unknown, treat all as potentially idling at stop line.
    halted = int(counts.get("halting_est") or counts.get("halted") or 0)
    class_total = sum(int(counts.get(k) or 0) for k in CLASS_KEYS)
    use_halt = halted > 0
    remaining = halted if use_halt else class_total
    for k in CLASS_KEYS:
        n = int(counts.get(k) or 0)
        if use_halt:
            take = min(n, remaining)
            remaining -= take
        else:
            take = n
        total += take * float(idle.get(k, idle.get("car", 837.0)))
    return total


def _unserved_idle(counts: dict[str, Any], phase: str, idle: dict[str, float]) -> float:
    served = set(PHASE_APPROACHES.get(phase, ()))
    return sum(
        _approach_idle_mg_s(vals, idle)
        for ap, vals in counts.items()
        if ap not in served and isinstance(vals, dict)
    )


def _served_pressure(pressures: dict[str, float], phase: str) -> float:
    return float(pressures.get(phase) or 0.0)


def decision_row(counts: dict[str, Any], frame: int, sample_idx: int, idle: dict[str, float]) -> dict[str, Any]:
    fuel = replay_decision(counts)  # fuel-weighted XtraFlow
    # Count-only pressure for contrast (same vehicles, no type weights)
    from sim.controllers import OursFuelController
    from sim.util import load_config
    from perception.replay import counts_to_vehicles

    cfg = load_config()
    ctrl = OursFuelController(cfg=cfg)
    alpha = float(cfg["controller"]["alpha_moving"])
    vehicles = counts_to_vehicles(counts)
    count_pressures = {
        p: ctrl.pressure(vehicles, p, use_weights=False, alpha=alpha) for p in PHASE_ORDER
    }
    count_phase = max(PHASE_ORDER, key=lambda p: count_pressures[p])

    fixed_phase = PHASE_ORDER[sample_idx % len(PHASE_ORDER)]
    xtra_idle = _unserved_idle(counts, fuel["next_phase"], idle)
    fixed_idle = _unserved_idle(counts, fixed_phase, idle)
    count_idle = _unserved_idle(counts, count_phase, idle)

    return {
        "frame": frame,
        "t_s": round(frame / 3.0, 2),  # counts sampled at ~3 fps in demo
        "next_phase": fuel["next_phase"],
        "count_phase": count_phase,
        "fixed_phase": fixed_phase,
        "n_vehicles": fuel["n_vehicles"],
        "pressures": {k: round(float(v), 3) for k, v in fuel["pressures"].items()},
        "served_pressure": round(_served_pressure(fuel["pressures"], fuel["next_phase"]), 3),
        "xtra_idle_mg_s": round(xtra_idle, 1),
        "fixed_idle_mg_s": round(fixed_idle, 1),
        "count_idle_mg_s": round(count_idle, 1),
        "idle_saved_vs_fixed_mg_s": round(fixed_idle - xtra_idle, 1),
    }


def series_from_counts_blob(blob: dict[str, Any]) -> list[dict[str, Any]]:
    idle = _weights().get("idle_fuel_mg_per_s", {})
    rows = []
    for i, step in enumerate(blob.get("series") or []):
        counts = step.get("counts") or {}
        rows.append(decision_row(counts, int(step.get("frame") or i), i, idle))
    return rows


def clubbed_counts_at(
    per_cam_blobs: dict[str, dict[str, Any]], index: int
) -> tuple[dict[str, Any], int]:
    """Fuse camera letter ROIs as if they were one intersection's N/E/S/W."""
    fused: dict[str, Any] = {a: _empty_approach() for a in ("N", "S", "E", "W")}
    frame = index
    for cam, blob in per_cam_blobs.items():
        series = blob.get("series") or []
        if not series:
            continue
        step = series[min(index, len(series) - 1)]
        frame = int(step.get("frame") or frame)
        # Prefer the camera's own approach polygon; fall back to total across ROIs.
        counts = step.get("counts") or {}
        own = counts.get(cam)
        if isinstance(own, dict) and sum(int(own.get(k) or 0) for k in CLASS_KEYS) > 0:
            fused[cam] = {**_empty_approach(), **{k: own.get(k, 0) for k in CLASS_KEYS},
                          "halting_est": own.get("halting_est", 0),
                          "weighted_pressure": own.get("weighted_pressure", 0)}
        else:
            # Aggregate all ROI hits onto this camera's approach (corridor cams).
            agg = _empty_approach()
            for vals in counts.values():
                if not isinstance(vals, dict):
                    continue
                for k in CLASS_KEYS:
                    agg[k] += int(vals.get(k) or 0)
                agg["halting_est"] += int(vals.get("halting_est") or 0)
                agg["weighted_pressure"] += float(vals.get("weighted_pressure") or 0)
            fused[cam] = agg
    return fused, frame


def clubbed_decision_series(per_cam_blobs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    idle = _weights().get("idle_fuel_mg_per_s", {})
    n = max((len(b.get("series") or []) for b in per_cam_blobs.values()), default=0)
    rows = []
    for i in range(n):
        counts, frame = clubbed_counts_at(per_cam_blobs, i)
        rows.append(decision_row(counts, frame, i, idle))
    return rows


def summarize_decisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    phase_counts: dict[str, int] = {}
    for r in rows:
        phase_counts[r["next_phase"]] = phase_counts.get(r["next_phase"], 0) + 1
    xtra = sum(r["xtra_idle_mg_s"] for r in rows)
    fixed = sum(r["fixed_idle_mg_s"] for r in rows)
    saved = fixed - xtra
    # Convert mg/s * sample ≈ mg for that sample interval (~1/3 s at 3 fps)
    dt = 1.0 / 3.0
    saved_L = (saved * dt) / 1e6  # mg → L roughly for petrol density ~1? actually fuel is mg, /1e6 = kg, petrol ~0.74 kg/L
    # Idle fuel in SUMO is mg/s; mg*s / 1e6 = grams/1000 = kg. Petrol ~740 g/L → L ≈ (mg * dt) / 1e6 / 0.74
    saved_L = (saved * dt) / 1e6 / 0.74
    return {
        "n_samples": len(rows),
        "phase_share": {k: round(v / len(rows), 3) for k, v in phase_counts.items()},
        "dominant_phase": max(phase_counts, key=phase_counts.get),
        "mean_served_pressure": round(sum(r["served_pressure"] for r in rows) / len(rows), 3),
        "cum_idle_xtra_mg": round(xtra * dt, 1),
        "cum_idle_fixed_mg": round(fixed * dt, 1),
        "cum_idle_saved_mg": round(saved * dt, 1),
        "idle_fuel_saved_L_proxy": round(saved_L, 5),
        "pct_idle_cut_vs_fixed": round(100.0 * saved / fixed, 2) if fixed > 0 else 0.0,
        "note": (
            "Idle-fuel proxy from detector counts + measured idle mg/s weights. "
            "Illustrative for this clip — not the published SUMO fuel result."
        ),
    }


def load_published_savings() -> list[dict[str, Any]]:
    path = ROOT / "results" / "headlines.json"
    if not path.exists():
        return []
    rows = []
    for h in load_json(path):
        rows.append(
            {
                "scenario": h.get("scenario"),
                "vs_best_baseline_%": round(float(h.get("pct_fuel_reduction_vs_best") or 0), 2),
                "vs_fixed_legacy_%": round(float(h.get("pct_fuel_reduction_vs_fixed_legacy") or 0), 2),
                "best_baseline": h.get("best_baseline"),
            }
        )
    return rows


def enrich_summary(summary: dict[str, Any], yolo_dir: Path | None = None) -> dict[str, Any]:
    """Attach decision timelines to an existing yolo_demo_summary dict."""
    yolo_dir = yolo_dir or (ROOT / "results" / "demo" / "yolo")
    per_blobs: dict[str, dict] = {}
    for approach, stats in (summary.get("per_camera") or {}).items():
        cpath = ROOT / (stats.get("counts") or f"results/demo/yolo/counts_{approach}.json")
        if not cpath.exists():
            cpath = yolo_dir / f"counts_{approach}.json"
        if not cpath.exists():
            continue
        blob = json.loads(cpath.read_text(encoding="utf-8"))
        per_blobs[approach] = blob
        decisions = series_from_counts_blob(blob)
        stats["decisions"] = decisions
        stats["decision_summary"] = summarize_decisions(decisions)

    club_rows = clubbed_decision_series(per_blobs) if per_blobs else []
    club = summary.setdefault("clubbed", {})
    club["decisions"] = club_rows
    club["decision_summary"] = summarize_decisions(club_rows)
    summary["published_fuel_savings"] = load_published_savings()
    summary["decision_label"] = (
        "XtraFlow serves the highest fuel-weighted phase pressure; "
        "idle-fuel proxy compares that choice to fixed round-robin on the same counts."
    )
    return summary


if __name__ == "__main__":
    import argparse
    from sim.util import save_json

    p = argparse.ArgumentParser()
    p.add_argument(
        "--summary",
        default=str(ROOT / "results" / "demo" / "yolo" / "yolo_demo_summary.json"),
    )
    args = p.parse_args()
    path = Path(args.summary)
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary = enrich_summary(summary)
    save_json(path, summary)
    club = summary["clubbed"].get("decision_summary") or {}
    print(json.dumps({"clubbed": club, "published": summary.get("published_fuel_savings")}, indent=2))
