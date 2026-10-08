"""Measure approach flows and degree of saturation on VALIDATION.

Webster reads measured_approach_veh_h from the file this writes. It does not
read the config demand table. If data/observed_counts.csv exists, flows are
scaled and GEH is computed from simulated detector counts.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from sim.util import ROOT, SCENARIOS, ensure_dirs, load_config, load_json, save_json, seed_range


def geh(obs: float, sim: float) -> float:
    return float(np.sqrt(2 * (sim - obs) ** 2 / max(sim + obs, 1e-9)))


def _sat_flow_veh_h() -> float:
    # Two effective through lanes at 1800 veh/h/lane. Assumed, not measured capacity.
    return 1800.0 * 2.0


def _dos(flow_veh_h: float, green_s: float, cycle_s: float) -> float:
    if cycle_s <= 0 or green_s <= 0:
        return float("nan")
    return (flow_veh_h / _sat_flow_veh_h()) / (green_s / cycle_s)


def main(smoke: bool = False) -> None:
    cfg = load_config()
    ensure_dirs()
    from sim.run_sim import run_one

    val = seed_range(cfg["seed_protocol"]["validation"])
    seeds = val[:1] if smoke else val[:5]
    scenarios = ["balanced"] if smoke else list(SCENARIOS)
    # Calibration runs before the green search. A smoke plan only covers one
    # scenario, so it must not be treated as the tuned baseline.
    controller = "fixed"
    tuned_path = ROOT / "results" / "fixed_tuned.json"
    if tuned_path.exists():
        tuned = load_json(tuned_path)
        plans = tuned.get("plans") or {}
        if not tuned.get("smoke") and all(s in plans for s in scenarios):
            controller = "fixed_tuned"
    obs_path = ROOT / "data" / "observed_counts.csv"
    scales: Dict[str, float] = {}
    mode = "measured_on_validation"
    if obs_path.exists():
        obs = pd.read_csv(obs_path)
        base = cfg["demand"]["balanced"]
        for _, row in obs.iterrows():
            ap = str(row["approach"])
            target = float(row["count_veh_h"])
            scales[ap] = target / max(float(base.get(ap, 1.0)), 1.0)
        mode = "scaled_to_observed_counts"

    measured: Dict[str, Dict[str, float]] = {}
    dos: Dict[str, Dict[str, float]] = {}
    geh_scores: Dict[str, float] = {}
    rows = []
    for scenario in scenarios:
        acc = {a: [] for a in ["N", "S", "E", "W"]}
        green_acc = {p: [] for p in ["NS_TL", "NS_R", "EW_TL", "EW_R"]}
        for seed in seeds:
            row = run_one(
                scenario, controller, seed, smoke=smoke,
                demand_scale=scales or None,
                run_id="calib",
            )
            flows = row.get("approach_veh_h") or {}
            for a in acc:
                if a in flows:
                    acc[a].append(float(flows[a]))
            for p, sec in (row.get("green_alloc_s") or {}).items():
                if p in green_acc:
                    green_acc[p].append(float(sec))
            rows.append({
                "scenario": scenario,
                "seed": seed,
                "status": row.get("status"),
                "n_departed": row.get("n_departed"),
                "approach_veh_h": flows,
            })
            if obs_path.exists() and scenario == "balanced":
                obs = pd.read_csv(obs_path)
                for _, orow in obs.iterrows():
                    ap = str(orow["approach"])
                    target = float(orow["count_veh_h"])
                    sim_h = float(flows.get(ap, 0.0))
                    geh_scores[ap] = geh(target, sim_h)
        measured[scenario] = {a: float(np.mean(v)) if v else 0.0 for a, v in acc.items()}
        # Degree of saturation from measured flow and the green this controller actually served.
        cycle = sum(float(np.mean(v)) if v else 0.0 for v in green_acc.values())
        # green_alloc is green seconds over the run, not a cycle. Convert to a fraction.
        dos[scenario] = {}
        for phase, approaches in (("NS_TL", ("N", "S")), ("EW_TL", ("E", "W"))):
            g = float(np.mean(green_acc[phase])) if green_acc[phase] else 0.0
            share = g / cycle if cycle else 0.0
            flow = max(measured[scenario][a] for a in approaches)
            # g/C is the green fraction. DoS = (v/s) / (g/C).
            dos[scenario][phase] = _dos(flow, share, 1.0) if share else float("nan")

    report = {
        "mode": mode,
        "label": "Simulation-based estimate; assumed traffic mix",
        "controller": controller,
        "seeds": seeds,
        "seed_split": "VALIDATION",
        "smoke": smoke,
        "measured_approach_veh_h": measured,
        "degree_of_saturation": dos,
        "saturation_assumption_veh_h": _sat_flow_veh_h(),
        "geh": geh_scores,
        "geh_target": 5.0,
        "scales": scales,
        "note": (
            "Approach veh/h are counts of vehicles seen on each incoming edge, "
            "scaled to an hour from the simulated duration. "
            "Degree of saturation uses an assumed 1800 veh/h/lane and the green fraction served. "
            "The config demand table is not copied into measured_approach_veh_h."
        ),
    }
    save_json(ROOT / "results" / "demand_calibration.json", report)
    print(mode, controller)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    main(smoke=args.smoke)
