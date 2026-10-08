"""Train PPO on TRAIN seeds across all four scenarios. Select on VALIDATION.

Non-smoke training runs at least config rl.total_timesteps. Checkpoint choice
uses full-horizon VALIDATION runs on every scenario. TEST seeds are not used.
"""
from __future__ import annotations

import hashlib
import os
from multiprocessing import get_context

import numpy as np

from sim.rl_env import SumoTrafficEnv
from sim.util import ROOT, SCENARIOS, ensure_dirs, load_config, save_json, seed_range


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _val_job(task):
    path, scenario, seed, smoke, horizon = task
    from stable_baselines3 import PPO
    from sim.run_sim import run_one
    model = PPO.load(str(path))
    row = run_one(
        scenario, "rl_ppo", seed, smoke=smoke,
        horizon_s=horizon, model=model, run_id="rlval",
    )
    if row.get("status") == "ok":
        return float(row["fuel_per_vehicle_L"])
    return None


def _score_checkpoint(path, scenarios, seeds, smoke, horizon) -> list:
    tasks = [(path, sc, sd, smoke, horizon) for sc in scenarios for sd in seeds]
    workers = 1 if smoke else min(6, max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(tasks) <= 1:
        fuels = [v for v in (_val_job(t) for t in tasks) if v is not None]
    else:
        print(f"rl validation {path.name}: {len(tasks)} runs, {workers} workers", flush=True)
        ctx = get_context("spawn")
        with ctx.Pool(workers) as pool:
            fuels = [v for v in pool.imap_unordered(_val_job, tasks, chunksize=1) if v is not None]
    return fuels


def main(smoke: bool = False, resume: str | None = None, val_seeds_n: int | None = None) -> None:
    cfg = load_config()
    ensure_dirs()
    from stable_baselines3 import PPO

    train_seeds = seed_range(cfg["seed_protocol"]["train"])
    val_seeds = seed_range(cfg["seed_protocol"]["validation"])
    if val_seeds_n and not smoke:
        val_seeds = val_seeds[: int(val_seeds_n)]
    scenarios = list(SCENARIOS)
    budget = int(cfg["rl"]["total_timesteps"])
    if smoke:
        train_seeds = train_seeds[:2]
        val_seeds = val_seeds[:1]
        scenarios = ["balanced"]
        budget = 256
    horizon = 60 if smoke else int(cfg["simulation"]["demand_horizon_s"])

    out_dir = ROOT / "results" / "rl"
    out_dir.mkdir(parents=True, exist_ok=True)
    curves = []

    class MultiScenarioEnv(SumoTrafficEnv):
        def __init__(self):
            super().__init__(scenario=scenarios[0], seed=train_seeds[0], smoke=smoke, cfg=cfg)
            self.horizon = horizon
            self.timeout = horizon + (30 if smoke else 60)
            self._i = 0
            self._pairs = [(sc, s) for sc in scenarios for s in train_seeds]

        def reset(self, *, seed=None, options=None):
            sc, sd = self._pairs[self._i % len(self._pairs)]
            self._i += 1
            self.scenario = sc
            self._seed = sd
            self.horizon = horizon
            self.timeout = horizon + (30 if smoke else 60)
            return super().reset(seed=sd, options=options)

    env = MultiScenarioEnv()
    n_steps = 128 if smoke else int(cfg["rl"]["n_steps"])
    batch = min(int(cfg["rl"]["batch_size"]), n_steps)
    # Train the full budget. Do not stop early below total_timesteps.
    chunk = max(n_steps, budget // 4)
    ckpts = []
    if resume and not smoke:
        resume_path = ROOT / resume
        model = PPO.load(str(resume_path), env=env, device="cpu")
        done_steps = int(model.num_timesteps)
        ckpts.append(resume_path)
        print(f"resume {resume} at {done_steps}/{budget}", flush=True)
    else:
        model = PPO(
            "MlpPolicy",
            env,
            learning_rate=float(cfg["rl"]["learning_rate"]),
            n_steps=n_steps,
            batch_size=batch,
            gamma=float(cfg["rl"]["gamma"]),
            verbose=1,
            seed=42,
        )
        done_steps = 0
    while done_steps < budget:
        step_now = min(chunk, budget - done_steps)
        model.learn(total_timesteps=step_now, reset_num_timesteps=False)
        done_steps += step_now
        path = out_dir / f"ppo_step{done_steps}.zip"
        model.save(str(path))
        ckpts.append(path)
        curves.append({"timesteps": done_steps, "checkpoint": path.name})
        print(f"timesteps={done_steps}/{budget}")

    env.close()

    def val_fuel(path) -> float:
        fuels = _score_checkpoint(path, scenarios, val_seeds, smoke, horizon if smoke else None)
        if not fuels:
            return float("inf")
        return float(np.mean(fuels))

    best_path = out_dir / "ppo_best.zip"
    best_score = float("inf")
    wrote_best = False
    # Score the final checkpoint. A resumed run does not re-score earlier chunks.
    candidates = [ckpts[-1]] if ckpts else []
    scored = []
    for path in candidates:
        score = val_fuel(path)
        scored.append({"checkpoint": path.name, "val_fuel_per_vehicle_L": score})
        print(f"val {path.name} {score}")
        if score < best_score:
            best_score = score
            PPO.load(str(path)).save(str(best_path))
            wrote_best = True

    # An older zip must not block the checkpoint this run just trained.
    if not wrote_best and ckpts:
        PPO.load(str(ckpts[-1])).save(str(best_path))
        wrote_best = True

    meta = {
        "selected_on": (
            f"VALIDATION seeds {list(val_seeds)}, all scenarios, full horizon, final checkpoint"
            if not smoke else "VALIDATION smoke horizon"
        ),
        "train_seeds": list(train_seeds),
        "confirm_seeds": list(val_seeds),
        "scenarios": scenarios,
        "best_path": "results/rl/ppo_best.zip",
        "best_val_fuel_per_vehicle_L": None if best_score == float("inf") else best_score,
        "smoke": smoke,
        "timesteps_budget": budget,
        "config_total_timesteps": int(cfg["rl"]["total_timesteps"]),
        "sha256": _sha256(best_path) if best_path.exists() else None,
        "scored_checkpoints": scored,
        "note": "Baseline trained with the recorded timestep budget. Not an oracle controller.",
    }
    save_json(out_dir / "training_curve.json", {"curve": curves, "scored": scored})
    save_json(out_dir / "rl_selection.json", meta)
    print(meta)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--resume", default=None, help="Relative checkpoint to continue until the config budget")
    p.add_argument("--val-seeds", type=int, default=None, help="Use only the first N VALIDATION seeds")
    args = p.parse_args()
    main(smoke=args.smoke, resume=args.resume, val_seeds_n=args.val_seeds)
