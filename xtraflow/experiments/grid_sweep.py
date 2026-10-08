"""2x2 grid with per-junction phase groups and a real coordination term.

Full demand horizon plus the same drain/gridlock logic as the single intersection.
Until a non-smoke run finishes, published text must not claim a grid result.
"""
from __future__ import annotations

import os
from multiprocessing import get_context

from sim.build_grid import junction_specs
from sim.gen_demand import generate_grid
from sim.util import ROOT, load_config, load_json, save_json, seed_range


def _specs(controller: str) -> list:
    info_path = ROOT / "results" / "grid_info.json"
    if info_path.exists():
        info = load_json(info_path)
        junctions = info.get("junctions") or {}
    else:
        junctions = {}
    if not junctions:
        live = junction_specs()
        junctions = live["junctions"]
        tls_ids = live["tls_ids"]
    else:
        tls_ids = list(junctions.keys())
    name = controller
    specs = []
    for tid in tls_ids:
        spec = dict(junctions[tid])
        spec["tls_id"] = tid
        spec["controller"] = name
        spec["neighbor_ids"] = spec.get("neighbor_ids") or [x for x in tls_ids if x != tid]
        specs.append(spec)
    return specs


def run_grid(controller: str, seed: int, smoke: bool = False) -> dict:
    from sim.run_sim import run_one

    cfg = load_config()
    net = ROOT / "results" / "networks" / "grid2x2.net.xml"
    if not net.exists():
        from sim.build_grid import build
        build()
    routes = generate_grid("balanced", seed, cfg)
    row = run_one(
        "balanced",
        controller,
        seed,
        cfg=cfg,
        smoke=smoke,
        net_path=net,
        routes_path=routes,
        junction_specs=_specs(controller),
        run_id="grid",
    )
    return {
        "controller": controller,
        "seed": seed,
        "fuel_per_vehicle_L": row["fuel_per_vehicle_L"],
        "mean_waiting_s": row["mean_waiting_s"],
        "n_completed": row["n_completed"],
        "n_departed": row["n_departed"],
        "n_unfinished": row["n_unfinished"],
        "status": row["status"],
        "error": row.get("error") or "",
        "wall_time_s": row["wall_time_s"],
    }


def _grid_task(task):
    ctrl, seed, smoke = task
    try:
        return run_grid(ctrl, seed, smoke=smoke)
    except Exception as e:  # noqa: BLE001
        return {
            "controller": ctrl, "seed": seed, "status": "error", "error": str(e),
            "fuel_per_vehicle_L": float("nan"), "mean_waiting_s": float("nan"),
            "n_completed": 0, "n_departed": 0, "n_unfinished": 0, "wall_time_s": -1,
        }


def main(smoke: bool = False, max_seeds: int | None = None) -> None:
    cfg = load_config()
    seeds = [1] if smoke else seed_range(cfg["seed_protocol"]["test"])
    if max_seeds and not smoke:
        seeds = seeds[: int(max_seeds)]
    controllers = ["fixed", "fixed_tuned", "actuated", "XtraFlow", "XtraFlow_coord"]
    tasks = [(ctrl, seed, smoke) for ctrl in controllers for seed in seeds]
    workers = 1 if smoke else min(4, max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1:
        rows = [_grid_task(t) for t in tasks]
    else:
        print(f"grid tasks={len(tasks)} workers={workers}", flush=True)
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            rows = list(pool.imap_unordered(_grid_task, tasks, chunksize=1))

    import numpy as np
    import pandas as pd

    df = pd.DataFrame(rows)
    ok = df[df.status == "ok"] if "status" in df.columns else df
    summary = {}
    if len(ok):
        summary = ok.groupby("controller")[["fuel_per_vehicle_L", "mean_waiting_s", "n_completed", "n_unfinished"]].mean().to_dict()
    indep = ok[ok.controller == "XtraFlow"]["fuel_per_vehicle_L"] if len(ok) else pd.Series(dtype=float)
    coord = ok[ok.controller == "XtraFlow_coord"]["fuel_per_vehicle_L"] if len(ok) else pd.Series(dtype=float)
    coord_helps = bool(len(indep) and len(coord) and np.isfinite(coord.mean()) and coord.mean() < indep.mean())
    errors = int((df.status == "error").sum()) if "status" in df.columns else 0
    out = {
        "rows": rows,
        "summary_means": summary,
        "coordination_reduces_fuel": coord_helps,
        "n_error_rows": errors,
        "smoke": smoke,
        "label": "Simulation-based estimate; assumed traffic mix",
        "note": "Per-junction groups from the grid net. Coordination subtracts neighbor downstream occupancy.",
    }
    save_json(ROOT / "results" / "grid_results.json", out)
    if errors and not len(ok):
        print(f"grid sweep: {errors} error rows; no ok rows recorded", flush=True)
        return
    if errors:
        print(f"grid sweep: {errors} error rows; continuing with ok rows only", flush=True)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4))
    if len(ok):
        g = ok.groupby("controller")["fuel_per_vehicle_L"].mean()
        ax.bar(g.index.astype(str), g.values)
    ax.set_ylabel("fuel per departed vehicle (L)")
    ax.set_title("Grid 2x2")
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "figures" / "x_grid_results.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--max-seeds", type=int, default=None)
    args = p.parse_args()
    main(smoke=args.smoke, max_seeds=args.max_seeds)
