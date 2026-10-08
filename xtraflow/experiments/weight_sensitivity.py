"""Sensitivity of XtraFlow fuel to the class fuel weights.

Uniformly scaling every w_type by the same constant leaves phase argmax unchanged
(pressure is linear in w). This study therefore only varies *relative* weights:
stretching deviations from car=1, and named ablations that change class ratios.

Writes results/weight_sensitivity.json.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Tuple

import numpy as np

from sim.util import ROOT, load_config, load_json, save_json


# Stretch of (w/w_car - 1). 1 → calibrated; >1 exaggerates gaps; 0.5 half-gaps.
# stretch=0 is identical to the "equal" ablation, so it is not scheduled here.
STRETCHES = [0.5, 1.0, 1.5, 2.0]
# Named ablations must change at least one class ratio vs calibrated (car=1).
ABLATIONS = ("calibrated_stretch", "equal", "no_2w_discount", "auto_as_truck")


def _base_weights() -> Dict[str, float]:
    data = load_json(ROOT / "results" / "weights.json")
    return {k: float(v) for k, v in data["w_type"].items()}


def _normalize_to_car(w: Dict[str, float]) -> Dict[str, float]:
    ref = float(w.get("car", 1.0)) or 1.0
    return {k: float(v) / ref for k, v in w.items()}


def _variant(name: str, stretch: float, base: Dict[str, float]) -> Dict[str, float]:
    """Return class weights with car normalized to 1.0.

    ``calibrated_stretch``: w_k = 1 + stretch * (base_k/base_car - 1).
    stretch=0 → all ones; stretch=1 → calibrated ratios; stretch>1 exaggerates.
    A uniform multiply-then-renormalize would cancel and is never used here.
    """
    rel = _normalize_to_car(base)
    if name == "equal":
        return {k: 1.0 for k in rel}
    if name == "no_2w_discount":
        w = dict(rel)
        w["two_wheeler"] = 1.0
        return w
    if name == "auto_as_truck":
        # Stress-test the passenger-car emission proxy by giving auto the truck weight.
        w = dict(rel)
        w["auto_rickshaw"] = float(rel.get("truck", rel["car"]))
        return w
    if name == "calibrated_stretch":
        s = float(stretch)
        return {k: 1.0 + s * (float(v) - 1.0) for k, v in rel.items()}
    raise ValueError(f"unknown weight variant: {name}")


def variants_are_distinct(base: Dict[str, float] | None = None) -> List[str]:
    """Return human-readable failures if any scheduled (name, stretch) collides."""
    base = base or _base_weights()
    seen: Dict[Tuple[float, ...], str] = {}
    failures: List[str] = []
    keys = sorted(base)
    for name in ABLATIONS:
        stretches = STRETCHES if name == "calibrated_stretch" else [1.0]
        for stretch in stretches:
            w = _variant(name, stretch, base)
            sig = tuple(round(w[k], 8) for k in keys)
            label = f"{name}@stretch={stretch:g}"
            if sig in seen:
                failures.append(f"{label} identical to {seen[sig]}")
            else:
                seen[sig] = label
            # Guard: uniform global scale of calibrated must not appear as a "scale".
            if name == "calibrated_stretch" and abs(stretch - 1.0) > 1e-12:
                cal = _variant("calibrated_stretch", 1.0, base)
                if all(abs(w[k] - cal[k]) < 1e-12 for k in keys):
                    failures.append(f"{label} collapsed to calibrated (cancel bug)")
    return failures


def _run_with_weights(scenario, seed, smoke, weights, variant, stretch) -> Dict[str, Any]:
    import sim.run_sim as run_mod

    orig = run_mod.make_controller

    def _make(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["weights"] = weights
        return orig(*args, **kwargs)

    run_mod.make_controller = _make  # type: ignore[assignment]
    try:
        row = run_mod.run_one(
            scenario,
            "XtraFlow",
            seed=int(seed),
            smoke=smoke,
            run_id=f"wsens_{variant}_s{float(stretch):g}",
        )
    finally:
        run_mod.make_controller = orig  # type: ignore[assignment]
    return {
        "variant": variant,
        "stretch": float(stretch),
        "scale": float(stretch),  # alias kept for older readers
        "scenario": scenario,
        "seed": int(seed),
        "weights": weights,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "mean_waiting_s": row["mean_waiting_s"],
        "n_completed": row["n_completed"],
        "status": row["status"],
    }


def _task(task: Tuple[Any, ...]) -> Dict[str, Any]:
    variant, stretch, scenario, seed, smoke = task
    weights = _variant(variant, stretch, _base_weights())
    return _run_with_weights(scenario, seed, smoke, weights, variant, stretch)


def main(smoke: bool = False, max_seeds: int | None = None) -> None:
    from multiprocessing import get_context

    base = _base_weights()
    bad = variants_are_distinct(base)
    if bad:
        raise SystemExit("weight variants cancel or collide: " + "; ".join(bad))

    scenarios = ["low_demand"] if smoke else ["balanced", "low_demand"]
    seeds = [1] if smoke else [1, 2, 3]
    if max_seeds is not None:
        seeds = seeds[: int(max_seeds)]

    tasks: List[Tuple[Any, ...]] = []
    for variant in ABLATIONS:
        stretches = STRETCHES if variant == "calibrated_stretch" else [1.0]
        for stretch in stretches:
            for sc in scenarios:
                for seed in seeds:
                    tasks.append((variant, stretch, sc, seed, smoke))

    workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))
    print(f"weight_sensitivity tasks={len(tasks)} workers={workers}", flush=True)
    if workers <= 1 or len(tasks) <= 1:
        rows = [_task(t) for t in tasks]
    else:
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            rows = list(pool.imap_unordered(_task, tasks, chunksize=1))

    summary = []
    for variant in ABLATIONS:
        stretches = STRETCHES if variant == "calibrated_stretch" else [1.0]
        for stretch in stretches:
            for sc in scenarios:
                vals = [
                    r["fuel_per_vehicle_L"] for r in rows
                    if r["variant"] == variant and abs(r["stretch"] - float(stretch)) < 1e-12
                    and r["scenario"] == sc and r["status"] == "ok"
                ]
                if not vals:
                    continue
                summary.append({
                    "variant": variant,
                    "stretch": float(stretch),
                    "scenario": sc,
                    "n": len(vals),
                    "fuel_per_vehicle_L_mean": float(np.mean(vals)),
                    "fuel_per_vehicle_L_std": float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
                    "weights": _variant(variant, stretch, base),
                })

    out = {
        "label": "assumed weight sensitivity",
        "base_weights": base,
        "note": (
            "Pressure is linear in class weights, so a global scale of w_type cancels in "
            "phase choice. calibrated_stretch varies deviations from car=1 "
            "(stretch=1 calibrated; stretch≠1 changes relative gaps). "
            "equal is the all-ones ablation (same as stretch=0). "
            "auto_as_truck replaces the no-op car_like_auto ablation "
            "(auto already equals car under the emission proxy). "
            "Does not retune the controller."
        ),
        "rows": rows,
        "summary": summary,
    }
    dest = ROOT / "results" / "weight_sensitivity.json"
    save_json(dest, out)
    print(f"wrote {dest} rows={len(rows)} summary={len(summary)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--max-seeds", type=int, default=None)
    args = p.parse_args()
    main(smoke=args.smoke, max_seeds=args.max_seeds)
