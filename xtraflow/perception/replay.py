"""Drive one XtraFlow decision from a perception counts JSON. No SUMO."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from sim.controllers import PHASE_ORDER, OursFuelController
from sim.util import ROOT, load_config, load_json


def counts_to_vehicles(counts: Dict[str, Any]) -> List[Dict[str, Any]]:
    vehicles = []
    n = 0
    for approach, classes in counts.items():
        if not isinstance(classes, dict):
            continue
        halted_budget = int(classes.get("halting_est") or classes.get("halted") or 0)
        used_halted = 0
        for vtype, raw in classes.items():
            if vtype in ("halting_est", "halted", "weighted_pressure"):
                continue
            try:
                count = int(raw)
            except (TypeError, ValueError):
                continue
            for _ in range(count):
                halting = used_halted < halted_budget
                if halting:
                    used_halted += 1
                vehicles.append({
                    "id": f"{approach}-{n}",
                    "approach": approach,
                    "vtype": vtype,
                    "halting": halting,
                    "turn": "through",
                })
                n += 1
    return vehicles


def replay_decision(counts: Dict[str, Any], cfg: dict | None = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    ctrl = OursFuelController(cfg=cfg)
    alpha = float(cfg["controller"]["alpha_moving"])
    vehicles = counts_to_vehicles(counts)
    pressures = {
        p: ctrl.pressure(vehicles, p, use_weights=True, alpha=alpha) for p in PHASE_ORDER
    }
    nxt = max(PHASE_ORDER, key=lambda p: pressures[p])
    return {
        "pressures": pressures,
        "next_phase": nxt,
        "n_vehicles": len(vehicles),
        "label": "Input feasibility — not a fuel-saving result",
    }


def replay_file(path: Path, frame: int = 0) -> Dict[str, Any]:
    blob = load_json(path)
    series = blob.get("series") or []
    if not series:
        raise ValueError(f"No series in {path}")
    counts = series[min(frame, len(series) - 1)]["counts"]
    decision = replay_decision(counts)
    decision["video"] = blob.get("video")
    return decision


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--counts", default=str(ROOT / "results" / "perception_counts.json"))
    p.add_argument("--frame", type=int, default=0)
    args = p.parse_args()
    print(replay_file(Path(args.counts), args.frame))
