"""Grid-search XtraFlow hyperparameters on VALIDATION seeds only.

The search covers balanced, peak_unbalanced, dynamic, and low_demand.
The spread of combo scores is stored so the report can state it from data.
"""
from __future__ import annotations

import itertools
import os
from multiprocessing import get_context
from typing import Any, Dict, List

from sim.util import ROOT, SCENARIOS, load_config, save_json, seed_range


def _eval_task(task, stage):
    params, scenario, seed, smoke = task
    from sim.run_sim import run_one
    run_id = "tune_" + "_".join(f"{k}{params[k]}" for k in sorted(params))
    row = run_one(scenario, "XtraFlow", seed, smoke=smoke, params=params, run_id=run_id)
    return {
        **params,
        "seed": seed,
        "scenario": scenario,
        "stage": stage,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "n_completed": row["n_completed"],
        "n_unfinished": row.get("n_unfinished", 0),
        "status": row.get("status", ""),
        "gridlock_flag": row["gridlock_flag"],
    }


def _eval_task_mp(args):
    task, stage = args
    return _eval_task(task, stage)


def main(smoke: bool = False) -> Dict[str, Any]:
    cfg = load_config()
    all_val = seed_range(cfg["seed_protocol"]["validation"])
    screen_seeds = all_val[:1] if smoke else all_val[:5]
    confirm_seeds = all_val[:1] if smoke else all_val
    grid = cfg["tune"]
    keys = list(grid.keys())
    combos = [dict(zip(keys, c)) for c in itertools.product(*[grid[k] for k in keys])]
    scenarios = ["balanced"] if smoke else list(SCENARIOS)
    if smoke:
        combos = combos[:2]

    n_workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))

    def run_batch(param_list, seeds, stage):
        tasks = [(p, sc, s, smoke) for p in param_list for sc in scenarios for s in seeds]
        rows = []
        if n_workers == 1:
            for t in tasks:
                rows.append(_eval_task(t, stage))
        else:
            ctx = get_context("spawn")
            print(f"tune {stage}: {len(tasks)} runs, {n_workers} workers", flush=True)
            with ctx.Pool(n_workers) as pool:
                for r in pool.imap_unordered(_eval_task_mp, [(t, stage) for t in tasks]):
                    rows.append(r)
                    if len(rows) % 24 == 0 or len(rows) == len(tasks):
                        print(f"tune {stage} {len(rows)}/{len(tasks)}", flush=True)
        return rows

    def score_rows(rows: List[Dict[str, Any]]):
        scores: Dict[tuple, List[float]] = {}
        for r in rows:
            if r.get("status") not in ("ok", "") or r.get("gridlock_flag"):
                continue
            key = tuple(r[k] for k in keys)
            if r["fuel_per_vehicle_L"] == r["fuel_per_vehicle_L"]:
                scores.setdefault(key, []).append(r["fuel_per_vehicle_L"])
        return scores

    print(f"Tune screen: {len(combos)} combos × {len(scenarios)} scenarios × {len(screen_seeds)} seeds")
    screen_rows = run_batch(combos, screen_seeds, "screen")
    scores = score_rows(screen_rows)
    ranked = sorted(
        ((sum(v) / len(v), dict(zip(keys, k))) for k, v in scores.items() if v),
        key=lambda x: x[0],
    )
    if not ranked:
        raise RuntimeError("Tuning produced no successful rows")
    score_values = [s for s, _ in ranked]
    span = None
    if min(score_values) > 0 and len(score_values) > 1:
        span = (max(score_values) - min(score_values)) / min(score_values)
    finalists = [p for _, p in ranked[: 1 if smoke else 3]]

    print(f"Confirm top {len(finalists)} on {len(confirm_seeds)} VAL seeds")
    confirm_rows = run_batch(finalists, confirm_seeds, "confirm")
    cscores = score_rows(confirm_rows)
    best_key = min(cscores, key=lambda k: sum(cscores[k]) / len(cscores[k]))
    best = dict(zip(keys, best_key))
    best_score = sum(cscores[best_key]) / len(cscores[best_key])
    best["selected_on"] = "VALIDATION seeds only"
    best["scenarios"] = scenarios
    best["screen_seeds"] = screen_seeds
    best["confirm_seeds"] = confirm_seeds
    best["mean_fuel_per_vehicle_L"] = best_score
    best["screen_relative_span"] = span
    best["n_screen_combos"] = len(combos)
    best["smoke"] = smoke
    save_json(ROOT / "results" / "tuned_params.json", best)
    save_json(ROOT / "results" / "tune_grid_results.json", {
        "rows": screen_rows + confirm_rows,
        "best": best,
        "screen_ranking": [{"score": s, "params": p} for s, p in ranked],
        "screen_relative_span": span,
        "note": "Span is (max-min)/min of mean fuel across the screened combos. Selection uses VALIDATION only.",
    })
    print(f"Best params: {best}")
    return best


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    main(smoke=args.smoke)
