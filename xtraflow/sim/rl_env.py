"""Gymnasium environment wrapping SUMO/TraCI for PPO training.

Action 0 keeps the phase. Actions 1..4 request NS_TL, NS_R, EW_TL, EW_R.
The same min green, max green, yellow, and all-red rules as the other controllers apply.
Observation features are scaled to [0, 1].
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover
    import gym
    from gym import spaces

from sim.controllers import RLPPOController, _load_tuned, _load_weights
from sim.gen_demand import generate
from sim.run_sim import _write_sumocfg
from sim.util import ROOT, load_config, locate_sumo, tool_cmd


class SumoTrafficEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, scenario: str = "balanced", seed: int = 1000, smoke: bool = False,
                 cfg: Optional[dict] = None):
        super().__init__()
        self.cfg = cfg or load_config()
        self.scenario = scenario
        self._seed = seed
        self.smoke = smoke
        self.horizon = 60 if smoke else int(self.cfg["simulation"]["demand_horizon_s"])
        self.timeout = self.horizon + (30 if smoke else int(self.horizon))
        self.weights = _load_weights()
        self.params = _load_tuned()
        self.observation_space = spaces.Box(low=0.0, high=1.0, shape=(RLPPOController.OBS_DIM,), dtype=np.float32)
        self.action_space = spaces.Discrete(5)  # 0 keep, 1..4 choose phase
        self._traci = None
        self._ctrl: Optional[RLPPOController] = None
        self._step_count = 0
        self._sumo_bin = None
        self._dead = False

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        if seed is not None:
            self._seed = seed
        if self._traci is not None:
            try:
                self._traci.close()
            except Exception:
                pass
        locate_sumo()
        import traci

        self._traci = traci
        routes = generate(self.scenario, self._seed, self.cfg)
        net = ROOT / "results" / "networks" / "intersection.net.xml"
        vtypes = ROOT / "results" / "networks" / "vtypes.add.xml"
        tls_add = ROOT / "results" / "networks" / "tls.add.xml"
        tag = f"rl_{self.scenario}_seed{self._seed}_{os.getpid()}"
        raw = ROOT / "results" / "rl"
        raw.mkdir(parents=True, exist_ok=True)
        tripinfo = raw / f"{tag}.tripinfo.xml"
        ssm = raw / f"{tag}.ssm.xml"
        cfg_path = raw / f"{tag}.sumocfg"
        _write_sumocfg(net, routes, [vtypes, tls_add], cfg_path, tripinfo, ssm, end=None)
        label = f"rl_{self.scenario}_{self._seed}_{os.getpid()}"
        cmd = tool_cmd("sumo") + ["-c", str(cfg_path), "--duration-log.disable", "true"]
        traci.start(cmd, label=label, numRetries=10)
        try:
            traci.switch(label)
        except Exception:
            pass
        try:
            traci.trafficlight.setProgram("C", "fixed")
        except Exception:
            pass
        self._ctrl = RLPPOController(
            model=None, cfg=self.cfg, weights=self.weights, params=self.params, allow_missing_model=True,
        )
        self._step_count = 0
        self._dead = False
        obs = self._ctrl.normalized_obs(traci)
        return obs, {}

    def step(self, action: int):
        traci = self._traci
        ctrl = self._ctrl
        if self._dead or ctrl is None:
            return np.zeros(self.observation_space.shape, dtype=np.float32), 0.0, False, True, {}
        try:
            ctrl._sim_time = float(self._step_count)
            switched = ctrl.apply_action(int(action))
            ctrl.apply_state(traci)
            traci.simulationStep()
        except Exception:
            self._dead = True
            return np.zeros(self.observation_space.shape, dtype=np.float32), -1.0, False, True, {}

        self._step_count += 1
        fuel_mg_s = 0.0
        try:
            for vid in traci.vehicle.getIDList():
                fuel_mg_s += float(traci.vehicle.getFuelConsumption(vid))
        except Exception:
            fuel_mg_s = 0.0
        lam = float(self.cfg["controller"].get("switch_penalty_rl", 0.05))
        # Negative fuel (mg/s -> a small litre-scale proxy) plus a switch penalty.
        reward = -(fuel_mg_s / 1e6) - lam * switched

        terminated = False
        truncated = self._step_count >= self.timeout
        if self._step_count >= self.horizon:
            try:
                if traci.simulation.getMinExpectedNumber() == 0:
                    terminated = True
            except Exception:
                truncated = True
        try:
            obs = ctrl.normalized_obs(traci)
        except Exception:
            self._dead = True
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
            truncated = True
        return obs, float(reward), terminated, truncated, {}

    def close(self):
        if self._traci is not None:
            try:
                self._traci.close()
            except Exception:
                pass
            self._traci = None
