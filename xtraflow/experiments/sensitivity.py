"""Demand multiplier and vehicle-mix Latin-hypercube sensitivity."""
from __future__ import annotations

import os
from multiprocessing import get_context

import numpy as np

from sim.util import ROOT, load_config, save_json


def lhs_mixes(n: int, seed: int = 0):
    """Latin hypercube over mix ranges (assumed)."""
    rng = np.random.default_rng(seed)
    # ranges assumed
    ranges = {
        "two_wheeler": (0.25, 0.55),
        "car": (0.15, 0.40),
        "auto_rickshaw": (0.05, 0.20),
        "bus": (0.02, 0.10),
        "truck": (0.05, 0.25),
    }
    keys = list(ranges.keys())
    u = (rng.random((n, len(keys))) + np.arange(n)[:, None]) / n
    for j in range(len(keys)):
        rng.shuffle(u[:, j])
    mixes = []
    for i in range(n):
        raw = {}
        for j, k in enumerate(keys):
            lo, hi = ranges[k]
            raw[k] = lo + u[i, j] * (hi - lo)
        s = sum(raw.values())
        mixes.append({k: v / s for k, v in raw.items()})
    return mixes, ranges


def _sens_task(task):
    kind, mult, seed, ctrl, smoke, mix = task
    from sim.run_sim import run_one
    if kind == "demand_mult":
        row = run_one("balanced", ctrl, seed, smoke=smoke, demand_mult=float(mult))
        return {
            "kind": "demand_mult", "mult": mult, "controller": ctrl, "seed": seed,
            "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
            "mean_waiting_s": row["mean_waiting_s"],
        }
    row = run_one("balanced", ctrl, seed=seed, smoke=smoke, mix_override=mix)
    return {
        "kind": "mix_lhs", "mix_id": seed - 1, "mix": mix, "controller": ctrl,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "mean_waiting_s": row["mean_waiting_s"],
    }


def main(smoke: bool = False, max_seeds: int | None = None, mix_samples: int | None = None) -> None:
    cfg = load_config()
    mults = cfg["sensitivity"]["demand_multipliers"]
    n_mix = 3 if smoke else int(cfg["sensitivity"]["mix_lhs_samples"])
    if mix_samples is not None and not smoke:
        n_mix = int(mix_samples)
    seeds = [1, 2] if smoke else [1, 2, 3, 4, 5]
    if max_seeds is not None and not smoke:
        seeds = seeds[: int(max_seeds)]
    scenario = "balanced"
    mixes, ranges = lhs_mixes(n_mix)
    tasks = [("demand_mult", m, seed, ctrl, smoke, None) for m in mults for seed in seeds for ctrl in ["fixed", "XtraFlow"]]
    tasks += [("mix_lhs", None, 1 + i, ctrl, smoke, mix) for i, mix in enumerate(mixes) for ctrl in ["fixed", "XtraFlow"]]
    workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(tasks) <= 1:
        rows = [_sens_task(t) for t in tasks]
    else:
        print(f"sensitivity tasks={len(tasks)} workers={workers}", flush=True)
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            rows = list(pool.imap_unordered(_sens_task, tasks, chunksize=1))

    # saturation proxy vs gain
    gains = []
    for m in mults:
        f = [r["fuel_per_vehicle_L"] for r in rows if r.get("kind") == "demand_mult" and r["mult"] == m and r["controller"] == "fixed"]
        o = [r["fuel_per_vehicle_L"] for r in rows if r.get("kind") == "demand_mult" and r["mult"] == m and r["controller"] == "XtraFlow"]
        if f and o:
            pct = (np.mean(f) - np.mean(o)) / np.mean(f) * 100
            gains.append({"demand_mult": m, "dos_proxy": m, "pct_fuel_reduction": float(pct)})

    out = {"rows": rows, "gains": gains, "mix_ranges_assumed": ranges, "label": "assumed"}
    save_json(ROOT / "results" / "sensitivity.json", out)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4))
    if gains:
        ax.plot([g["dos_proxy"] for g in gains], [g["pct_fuel_reduction"] for g in gains], marker="o")
    ax.set_xlabel("Demand multiplier (DoS proxy)")
    ax.set_ylabel("% fuel reduction XtraFlow vs fixed")
    ax.set_title("Gain vs degree of saturation (proxy)")
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "figures" / "vi_gain_vs_saturation.png", dpi=200)
    plt.close(fig)
    print("sensitivity done")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--max-seeds", type=int, default=None)
    p.add_argument("--mix-samples", type=int, default=None)
    args = p.parse_args()
    main(smoke=args.smoke, max_seeds=args.max_seeds, mix_samples=args.mix_samples)
