"""Fuel reduction under the primary emission map and a distinct alternate map.

The alternate keeps bus and truck off passenger-car classes. The verdict
compares confidence intervals, not only the sign of the mean.
"""
from __future__ import annotations

import os
from multiprocessing import get_context

import numpy as np

from experiments.analyze import bootstrap_ci
from sim.util import ROOT, SCENARIOS, load_config, load_json, save_json, seed_range


def _emis_task(task):
    scenario, emis, ctrl, seed, smoke = task
    from sim.run_sim import run_one
    row = run_one(scenario, ctrl, seed, smoke=smoke, emission_model=emis, run_id=f"emis_{emis}")
    return {
        "scenario": scenario,
        "emission_model": emis,
        "controller": ctrl,
        "seed": seed,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "total_CO2_kg": row["total_CO2_kg"],
        "status": row.get("status"),
    }


def _paired_pct(rows, emis, scenario):
    fmap = {
        r["seed"]: r["fuel_per_vehicle_L"]
        for r in rows
        if r["emission_model"] == emis and r["controller"] == "fixed" and r["scenario"] == scenario
    }
    omap = {
        r["seed"]: r["fuel_per_vehicle_L"]
        for r in rows
        if r["emission_model"] == emis and r["controller"] == "XtraFlow" and r["scenario"] == scenario
    }
    vals = []
    for s, fv in fmap.items():
        if s in omap and fv:
            vals.append((fv - omap[s]) / fv * 100.0)
    return np.asarray(vals, dtype=float)


def _verdict(lo_a, hi_a, lo_b, hi_b) -> str:
    if not all(np.isfinite(x) for x in (lo_a, hi_a, lo_b, hi_b)):
        return "insufficient_data"
    overlap = not (hi_a < lo_b or hi_b < lo_a)
    if overlap:
        return "ci_overlap"
    same_sign = (lo_a > 0 and lo_b > 0) or (hi_a < 0 and hi_b < 0)
    if same_sign:
        return "same_sign_magnitudes_differ"
    return "sign_disagrees"


def main(smoke: bool = False, max_seeds: int | None = None) -> None:
    cfg = load_config()
    seeds = seed_range(cfg["seed_protocol"]["test"])[:1] if smoke else seed_range(cfg["seed_protocol"]["test"])
    if max_seeds and not smoke:
        seeds = seeds[: int(max_seeds)]
    scenarios = ["balanced"] if smoke else list(SCENARIOS)

    tasks = [
        (scenario, emis, ctrl, seed, smoke)
        for scenario in scenarios
        for emis in ["primary", "alternate"]
        for ctrl in ["fixed", "XtraFlow"]
        for seed in seeds
    ]
    workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(tasks) <= 1:
        rows = [_emis_task(t) for t in tasks]
    else:
        print(f"emission tasks={len(tasks)} workers={workers}", flush=True)
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            rows = list(pool.imap_unordered(_emis_task, tasks, chunksize=1))

    by_model = {}
    for emis in ["primary", "alternate"]:
        all_pct = []
        per_scenario = {}
        for scenario in scenarios:
            pct = _paired_pct(rows, emis, scenario)
            mean, lo, hi = bootstrap_ci(pct, n_resamples=500 if smoke else 5000)
            per_scenario[scenario] = {"mean": mean, "ci_lo": lo, "ci_hi": hi, "n": int(len(pct))}
            all_pct.append(pct)
        stacked = np.concatenate(all_pct) if all_pct else np.asarray([])
        mean, lo, hi = bootstrap_ci(stacked, n_resamples=500 if smoke else 5000)
        by_model[emis] = {"mean": mean, "ci_lo": lo, "ci_hi": hi, "per_scenario": per_scenario}

    emap = load_json(ROOT / "results" / "emission_class_map.json") if (ROOT / "results" / "emission_class_map.json").exists() else {}
    a = by_model["primary"]
    b = by_model["alternate"]
    verdict = _verdict(a["ci_lo"], a["ci_hi"], b["ci_lo"], b["ci_hi"])
    out = {
        "rows": rows,
        "by_model": by_model,
        "pct_reduction_primary": a["mean"],
        "pct_reduction_alternate": b["mean"],
        "verdict": verdict,
        "emission_map": emap,
        "note": (
            "Alternate bus and truck classes are heavy-duty or bus, not a passenger car. "
            "auto-rickshaw uses a passenger-car proxy. two-wheeler uses LDV_G_EU4 when that class loads. "
            "Idle-fuel weights inherit the primary classes. "
            "Simulation-based estimate; assumed traffic mix."
        ),
        "label": "Simulation-based estimate; assumed traffic mix",
    }
    save_json(ROOT / "results" / "emission_xcheck.json", out)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(["primary", "alternate"], [a["mean"], b["mean"]])
    ax.set_ylabel("% fuel reduction XtraFlow vs fixed")
    ax.set_title(f"Emission cross-check ({verdict})")
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "figures" / "ix_emission_xcheck.png", dpi=200)
    plt.close(fig)
    print(verdict)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--max-seeds", type=int, default=None)
    args = p.parse_args()
    main(smoke=args.smoke, max_seeds=args.max_seeds)
