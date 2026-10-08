"""Full TEST sweep / smoke sweep over controllers × scenarios × seeds.

The sweep only checks the config lock. Create the lock with `make freeze_config`
before a non-smoke run. It does not rewrite the lock.
"""
from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

from sim.run_sim import RAW_FIELDS, append_raw_row, run_one
from sim.util import (
    CONTROLLER_NAMES,
    ROOT,
    SCENARIOS,
    assert_config_locked,
    ensure_dirs,
    load_config,
    rel_to_root,
    seed_range,
)


def _existing_keys(path: Path) -> set:
    if not path.exists():
        return set()
    keys = set()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames and list(r.fieldnames) != RAW_FIELDS:
            raise SystemExit(
                f"{path} schema does not match RAW_FIELDS. Archive it before resuming."
            )
        for row in r:
            keys.add((row["scenario"], row["controller"], str(row["seed"]), row.get("emission_model", "primary")))
    return keys


def _load_rl_model():
    best = ROOT / "results" / "rl" / "ppo_best.zip"
    if not best.exists():
        raise FileNotFoundError(
            f"results/rl/ppo_best.zip is missing. Refusing to run rl_ppo as always-keep. Missing: {best.name}"
        )
    from stable_baselines3 import PPO
    return PPO.load(str(best))


def _worker(task: Tuple) -> Dict[str, Any]:
    scenario, controller, seed, smoke, emission_model, save_ts = task
    try:
        model = None
        if controller == "rl_ppo":
            model = _load_rl_model()
        row = run_one(
            scenario,
            controller,
            seed,
            smoke=smoke,
            emission_model=emission_model,
            save_timeseries=save_ts,
            model=model,
        )
        row["status"] = row.get("status") or "ok"
        row["error"] = row.get("error") or ""
        return row
    except Exception as e:  # noqa: BLE001
        return {
            "scenario": scenario,
            "controller": controller,
            "seed": seed,
            "emission_model": emission_model,
            "n_departed": 0,
            "n_completed": 0,
            "n_unfinished": 0,
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "total_fuel_L": float("nan"),
            "fuel_per_vehicle_L": float("nan"),
            "fuel_per_person_L": float("nan"),
            "total_CO2_kg": float("nan"),
            "mean_waiting_s": float("nan"),
            "p95_waiting_s": float("nan"),
            "mean_timeLoss_s": float("nan"),
            "person_delay_s": float("nan"),
            "mean_travel_time_s": float("nan"),
            "stops_per_vehicle": float("nan"),
            "idle_vehicle_seconds": float("nan"),
            "mean_queue_veh": float("nan"),
            "max_queue_veh": float("nan"),
            "fuel_L_by_type": "{}",
            "ssm_conflicts": -1,
            "conflicts_per_1000_veh": float("nan"),
            "gridlock_flag": 0,
            "wall_time_s": -1,
        }


def _fail_if_errors(path: Path) -> None:
    if not path.exists():
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bad = [r for r in rows if r.get("status") == "error"]
    if bad:
        sample = "; ".join(f"{r['controller']}/{r['scenario']}/seed{r['seed']}: {r.get('error')}" for r in bad[:5])
        raise SystemExit(f"Sweep has {len(bad)} error rows. First: {sample}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--controllers", nargs="*", default=None)
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-seeds", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    ensure_dirs()

    net = ROOT / "results" / "networks" / "intersection.net.xml"
    if not net.exists():
        from sim.build_network import build
        build()

    if args.smoke:
        controllers = args.controllers or CONTROLLER_NAMES
        scenarios = args.scenarios or ["balanced"]
        seeds = [1]
        out_csv = ROOT / "results" / "raw_runs_smoke.csv"
    else:
        assert_config_locked()
        controllers = args.controllers or CONTROLLER_NAMES
        scenarios = args.scenarios or SCENARIOS
        seeds = seed_range(cfg["seed_protocol"]["test"])
        if args.max_seeds:
            seeds = seeds[: int(args.max_seeds)]
        out_csv = ROOT / "results" / "raw_runs.csv"
        print(f"TEST seeds={list(seeds)}")

    if "rl_ppo" in controllers and not (ROOT / "results" / "rl" / "ppo_best.zip").exists():
        raise SystemExit(
            "results/rl/ppo_best.zip is missing. Sweep will not run rl_ppo as an always-keep controller."
        )

    existing = _existing_keys(out_csv)
    tasks = []
    for sc in scenarios:
        for ctrl in controllers:
            for seed in seeds:
                key = (sc, ctrl, str(seed), "primary")
                if key in existing:
                    continue
                save_ts = (not args.smoke) and seed == seeds[0]
                tasks.append((sc, ctrl, seed, args.smoke, "primary", save_ts))

    auto = min(6, max(1, (os.cpu_count() or 2) - 1))
    n_workers = auto if args.workers is None else int(args.workers)
    n_workers = max(1, min(n_workers, 4 if args.smoke else max(1, (os.cpu_count() or 2) - 1)))

    print(f"Sweep tasks={len(tasks)} workers={n_workers} smoke={args.smoke}")
    if not tasks:
        _fail_if_errors(out_csv)
        print("Nothing to do (resumable: all keys present).")
        return

    ctx = mp.get_context("spawn")
    with ctx.Pool(n_workers) as pool:
        for row in tqdm(pool.imap_unordered(_worker, tasks), total=len(tasks)):
            append_raw_row(row, out_csv)

    _fail_if_errors(out_csv)
    print(f"Wrote/updated {rel_to_root(out_csv)}")


if __name__ == "__main__":
    main()
