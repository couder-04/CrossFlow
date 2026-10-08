"""Robustness of XtraFlow to detection miss rates. TEST seeds, after the lock.

Miss levels come from config. Reference lines are fixed_tuned, actuated, and maxpressure.
Output files differ by run_id so a 10% miss run does not overwrite a 0% run.
"""
from __future__ import annotations

import os
from multiprocessing import get_context

from sim.controllers import PerceptionNoise
from sim.util import ROOT, load_config, save_json, seed_range


def main(smoke: bool = False, max_seeds: int | None = None) -> None:
    cfg = load_config()
    seeds = seed_range(cfg["seed_protocol"]["test"])
    if smoke:
        seeds = seeds[:1]
    elif max_seeds:
        seeds = seeds[: int(max_seeds)]
    rates = cfg["robustness"]["detect_miss_rates"]
    scenario = "peak_unbalanced"
    references = ["fixed_tuned", "actuated", "maxpressure"]
    tasks = [("reference", ctrl, 0.0, seed, smoke) for ctrl in references for seed in seeds]
    tasks += [("XtraFlow", "XtraFlow", float(miss), seed, smoke) for miss in rates for seed in seeds]
    emp_path = ROOT / "perception" / "noise_model.json"
    if emp_path.exists():
        tasks += [("empirical", "XtraFlow", None, seed, smoke) for seed in seeds]
    workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(tasks) <= 1:
        rows = [_robust_task(t) for t in tasks]
    else:
        print(f"robustness tasks={len(tasks)} workers={workers}", flush=True)
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            rows = list(pool.imap_unordered(_robust_task, tasks, chunksize=1))

    out = {
        "scenario": scenario,
        "seeds": list(seeds),
        "rows": rows,
        "label": "Simulation-based estimate; assumed traffic mix",
    }
    save_json(ROOT / "results" / "robustness.json", out)

    import matplotlib.pyplot as plt
    import pandas as pd

    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(8, 4))
    ours = df[df.kind == "XtraFlow"].groupby("miss")["fuel_per_vehicle_L"].mean()
    if len(ours):
        ax.plot(ours.index, ours.values, marker="o", label="XtraFlow")
    for ctrl in references:
        sub = df[df.controller == ctrl]["fuel_per_vehicle_L"]
        if len(sub):
            ax.axhline(sub.mean(), linestyle="--", label=ctrl)
    ax.set_xlabel("detect_miss_rate")
    ax.set_ylabel("fuel per departed vehicle (L)")
    ax.set_title("Robustness to detection errors")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "figures" / "vii_robustness.png", dpi=200)
    plt.close(fig)
    print("robustness done")


def _robust_task(task):
    kind, ctrl, miss, seed, smoke = task
    from sim.run_sim import run_one

    scenario = "peak_unbalanced"
    noise = None
    run_id = "miss0"
    source = None
    if kind == "XtraFlow":
        noise = PerceptionNoise(detect_miss_rate=float(miss), mean_burst_s=3.0)
        run_id = f"miss{miss}"
    elif kind == "empirical":
        path = ROOT / "perception" / "noise_model.json"
        noise = PerceptionNoise.from_file(path)
        run_id = "miss_empirical"
        miss = noise.detect_miss_rate
        source = "empirical" if _source(path) == "empirical" else "assumed"
        kind = "empirical_noise" if noise else "noise"
    row = run_one(scenario, ctrl, seed, smoke=smoke, noise=noise, run_id=run_id)
    out = {
        "kind": kind,
        "controller": ctrl,
        "miss": miss,
        "seed": seed,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "mean_waiting_s": row["mean_waiting_s"],
        "status": row.get("status"),
        "n_unfinished": row.get("n_unfinished"),
    }
    if source is not None:
        out["source"] = source
    return out


def _source(path) -> str:
    from sim.util import load_json
    try:
        return str(load_json(path).get("source", ""))
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--max-seeds", type=int, default=None)
    args = p.parse_args()
    main(smoke=args.smoke, max_seeds=args.max_seeds)
