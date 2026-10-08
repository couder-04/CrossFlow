"""Traffic signal controllers sharing hard timing constraints.

Adaptive controllers read the same detector stream. `info_mode: oracle` (the default)
uses SUMO ground truth: speed, vehicle class, and route turn. `info_mode: camera`
applies the perception noise model and class map. That is an oracle detector unless
camera mode or an explicit PerceptionNoise is selected.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sim.util import ROOT, load_config, load_json

PHASE_ORDER = ["NS_TL", "NS_R", "EW_TL", "EW_R"]
APPROACHES = ["N", "S", "E", "W"]

# Per-phase out-edges for movements served by that phase (left-hand traffic).
# Built from connection geometry: each phase lists only the out-edges its greens feed.
# Prefer results/phase_groups.json when the network has been built.
DEFAULT_DOWNSTREAM = {
    "NS_TL": ["E_out", "S_out", "W_out", "N_out"],  # N L/T, S L/T
    "NS_R": ["W_out", "E_out"],                     # N right, S right
    "EW_TL": ["S_out", "W_out", "N_out", "E_out"],  # E L/T, W L/T
    "EW_R": ["N_out", "S_out"],                     # E right, W right
}
DEFAULT_APPROACH_EDGES = {a: [f"{a}_in"] for a in APPROACHES}


@dataclass
class PerceptionNoise:
    detect_miss_rate: float = 0.0
    class_confusion: Optional[Dict[str, Dict[str, float]]] = None
    count_jitter_std: float = 0.0
    mean_burst_s: float = 3.0

    @classmethod
    def from_file(cls, path: Path) -> "PerceptionNoise":
        if not path.exists():
            return cls()
        d = load_json(path)
        return cls(
            detect_miss_rate=float(d.get("detect_miss_rate", 0.0)),
            class_confusion=d.get("class_confusion"),
            count_jitter_std=float(d.get("count_jitter_std", 0.0)),
            mean_burst_s=float(d.get("mean_burst_s", 3.0)),
        )

    def active(self) -> bool:
        return (
            self.detect_miss_rate > 0
            or self.count_jitter_std > 0
            or bool(self.class_confusion)
        )


@dataclass
class ControllerState:
    phase_idx: int = 0
    time_in_phase: float = 0.0
    in_yellow: bool = False
    in_all_red: bool = False
    yellow_left: float = 0.0
    all_red_left: float = 0.0
    waiting_since: Dict[str, float] = field(default_factory=dict)
    last_switch_reason: str = ""


class BaseController:
    name = "base"

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, groups: Optional[Dict[str, List[int]]] = None,
                 n_links: int = 0, noise: Optional[PerceptionNoise] = None,
                 weights: Optional[Dict[str, float]] = None, params: Optional[Dict[str, Any]] = None,
                 approach_edges: Optional[Dict[str, List[str]]] = None,
                 downstream_edges: Optional[Dict[str, List[str]]] = None,
                 turn_lookup: Optional[Dict[str, str]] = None):
        self.cfg = cfg or load_config()
        phase_file = ROOT / "results" / "phase_groups.json"
        if groups is None:
            if not phase_file.exists():
                raise RuntimeError("results/phase_groups.json missing; build the network first")
            groups = load_json(phase_file)["groups"]
            n_links = n_links or int(load_json(phase_file)["n_links"])
        self.groups = groups
        self.n_links = n_links or max((max(v) for v in groups.values() if v), default=-1) + 1
        self.noise = noise or PerceptionNoise()
        self.weights = weights or {k: 1.0 for k in self.cfg["vehicle_mix"]}
        self.params = params or {}
        sig = self.cfg["signals"]
        self.min_green = float(sig["min_green_s"])
        self.max_green = float(sig["max_green_s"])
        self.yellow = float(sig["yellow_s"])
        self.all_red = float(sig["all_red_s"])
        self.starvation = float(sig["starvation_s"])
        self.state = ControllerState()
        self.tls_id = "C"
        self.approach_edges = approach_edges or {k: list(v) for k, v in DEFAULT_APPROACH_EDGES.items()}
        # Prefer geometry-inferred outs from phase_groups.json when present.
        if downstream_edges is None and phase_file.exists():
            pg = load_json(phase_file)
            downstream_edges = pg.get("downstream_edges")
        self.downstream_edges = downstream_edges or {k: list(v) for k, v in DEFAULT_DOWNSTREAM.items()}
        self.turn_lookup = turn_lookup or {}
        if not self.turn_lookup and phase_file.exists():
            self.turn_lookup = load_json(phase_file).get("turn_lookup") or {}
        self._rng_noise: Optional[random.Random] = None
        self._noise_seed = ""
        self._vid_rngs: Dict[tuple, random.Random] = {}
        self._miss_state: Dict[str, str] = {}
        self._class_memo: Dict[str, str] = {}
        self._sim_time = 0.0
        self.info_mode = str(self.cfg.get("controller", {}).get("info_mode", "oracle"))

    def set_noise_rng(self, rng: random.Random) -> None:
        self._rng_noise = rng
        # Fresh Random(same seed) objects share this state, so detections repeat.
        self._noise_seed = repr(rng.getstate())
        self._vid_rngs = {}
        self._miss_state = {}
        self._class_memo = {}

    def current_phase_name(self) -> str:
        return PHASE_ORDER[self.state.phase_idx % len(PHASE_ORDER)]

    def green_state(self, phase_name: str) -> str:
        state = ["r"] * self.n_links
        for idx in self.groups.get(phase_name, []):
            if 0 <= idx < self.n_links:
                state[idx] = "G"
        return "".join(state)

    def yellow_state(self, phase_name: str) -> str:
        state = ["r"] * self.n_links
        for idx in self.groups.get(phase_name, []):
            if 0 <= idx < self.n_links:
                state[idx] = "y"
        return "".join(state)

    def all_red_state(self) -> str:
        return "r" * self.n_links

    def apply_state(self, traci_mod) -> None:
        st = self.state
        if st.in_yellow:
            traci_mod.trafficlight.setRedYellowGreenState(
                self.tls_id, self.yellow_state(PHASE_ORDER[st.phase_idx])
            )
        elif st.in_all_red:
            traci_mod.trafficlight.setRedYellowGreenState(self.tls_id, self.all_red_state())
        else:
            traci_mod.trafficlight.setRedYellowGreenState(
                self.tls_id, self.green_state(PHASE_ORDER[st.phase_idx])
            )

    def _begin_switch(self, new_idx: int, reason: str) -> None:
        self.state.in_yellow = True
        self.state.yellow_left = self.yellow
        self.state._pending_idx = new_idx  # type: ignore[attr-defined]
        self.state.last_switch_reason = reason

    def _tick_transition(self) -> bool:
        """Advance yellow/all-red. Return True while a transition owns the step."""
        st = self.state
        if st.in_yellow:
            st.yellow_left -= 1.0
            st.time_in_phase += 1.0
            if st.yellow_left <= 0:
                st.in_yellow = False
                st.in_all_red = True
                st.all_red_left = self.all_red
            return True
        if st.in_all_red:
            st.all_red_left -= 1.0
            st.time_in_phase += 1.0
            if st.all_red_left <= 0:
                st.in_all_red = False
                st.phase_idx = getattr(st, "_pending_idx", (st.phase_idx + 1) % len(PHASE_ORDER))
                st.time_in_phase = 0.0
                self.state.waiting_since[PHASE_ORDER[st.phase_idx]] = self._sim_time
            return True
        return False

    def _vid_rng(self, vid: str, purpose: str) -> random.Random:
        key = (vid, purpose)
        if key not in self._vid_rngs:
            self._vid_rngs[key] = random.Random(f"{self._noise_seed}|{vid}|{purpose}")
        return self._vid_rngs[key]

    def _markov_miss(self, vid: str) -> bool:
        """2-state Markov dropout. Stationary miss rate matches detect_miss_rate.

        Mean miss-burst length is noise.mean_burst_s. The chain is per vehicle id,
        so a miss persists for a burst instead of flipping independently each second.
        """
        p_miss = float(self.noise.detect_miss_rate)
        if p_miss <= 0:
            self._miss_state[vid] = "visible"
            return False
        if p_miss >= 1:
            self._miss_state[vid] = "missed"
            return True
        burst = max(float(self.noise.mean_burst_s), 1.0)
        q_leave = min(1.0, 1.0 / burst)
        p_enter = min(1.0, p_miss * q_leave / (1.0 - p_miss))
        rng = self._vid_rng(vid, "markov")
        state = self._miss_state.get(vid)
        if state is None:
            state = "missed" if rng.random() < p_miss else "visible"
        if state == "visible":
            if rng.random() < p_enter:
                state = "missed"
        elif rng.random() < q_leave:
            state = "visible"
        self._miss_state[vid] = state
        return state == "missed"

    def _persistent_class(self, vid: str, vtype: str) -> str:
        if vid in self._class_memo:
            return self._class_memo[vid]
        confused = vtype
        table = self.noise.class_confusion or {}
        if vtype in table:
            probs = table[vtype]
            keys = list(probs.keys())
            weights = [float(probs[k]) for k in keys]
            confused = self._vid_rng(vid, "class").choices(keys, weights=weights, k=1)[0]
        self._class_memo[vid] = confused
        return confused

    def _apply_count_jitter(self, kept: List[Dict[str, Any]], dropped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        std = float(self.noise.count_jitter_std)
        if std <= 0 or self._rng_noise is None:
            return kept
        delta = int(round(self._rng_noise.gauss(0.0, std)))
        if delta < 0 and kept:
            n_drop = min(len(kept), -delta)
            order = list(range(len(kept)))
            self._rng_noise.shuffle(order)
            drop_ix = set(order[:n_drop])
            return [v for i, v in enumerate(kept) if i not in drop_ix]
        if delta > 0 and dropped:
            restored = dropped[:delta]
            return kept + restored
        return kept

    def _require_noise_rng(self) -> None:
        if self.noise.active() and self._rng_noise is None:
            raise RuntimeError("set_noise_rng() was not called; refusing Random(0)")

    def observe_vehicles(self, traci_mod, distance_m: float) -> List[Dict[str, Any]]:
        """Vehicles within distance_m of the stop line on this junction's approaches."""
        self._require_noise_rng()
        vehicles: List[Dict[str, Any]] = []
        for approach, edges in self.approach_edges.items():
            for edge in edges:
                try:
                    ids = traci_mod.edge.getLastStepVehicleIDs(edge)
                except Exception:
                    continue
                for vid in ids:
                    try:
                        lane_id = traci_mod.vehicle.getLaneID(vid)
                        lane_pos = traci_mod.vehicle.getLanePosition(vid)
                        lane_len = traci_mod.lane.getLength(lane_id)
                        dist_to_stop = lane_len - lane_pos
                        if dist_to_stop > distance_m:
                            continue
                        speed = traci_mod.vehicle.getSpeed(vid)
                        vtype = traci_mod.vehicle.getTypeID(vid)
                        try:
                            lane_index = int(str(lane_id).rsplit("_", 1)[-1])
                        except ValueError:
                            lane_index = 1
                        turn = "through"
                        try:
                            route = traci_mod.vehicle.getRoute(vid)
                            if edge in route:
                                i = list(route).index(edge)
                                if i + 1 < len(route):
                                    nxt = route[i + 1]
                                    key = f"{edge}|{nxt}"
                                    if key in self.turn_lookup:
                                        turn = self.turn_lookup[key]
                                    else:
                                        dest = str(nxt).replace("_out", "")
                                        if len(dest) == 1 and dest in APPROACHES and approach in APPROACHES:
                                            from sim.build_network import _classify_turn
                                            turn = _classify_turn(approach, dest)
                                        else:
                                            turn = {0: "left", 1: "through", 2: "right"}.get(lane_index, "through")
                            else:
                                turn = {0: "left", 1: "through", 2: "right"}.get(lane_index, "through")
                        except Exception:
                            turn = {0: "left", 1: "through", 2: "right"}.get(lane_index, "through")
                        vehicles.append({
                            "id": vid,
                            "approach": approach,
                            "vtype": vtype,
                            "halting": speed < 0.1,
                            "dist": dist_to_stop,
                            "lane_index": lane_index,
                            "turn": turn,
                        })
                    except Exception:
                        continue

        if not self.noise.active():
            return vehicles

        kept: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        for v in vehicles:
            if self._markov_miss(v["id"]):
                dropped.append(v)
                continue
            v["vtype"] = self._persistent_class(v["id"], v["vtype"])
            kept.append(v)
        return self._apply_count_jitter(kept, dropped)

    def pressure(self, vehicles: List[Dict[str, Any]], phase_name: str,
                 use_weights: bool, alpha: float, downstream_penalty: float = 0.0) -> float:
        approaches = {"NS_TL": ("N", "S"), "NS_R": ("N", "S"), "EW_TL": ("E", "W"), "EW_R": ("E", "W")}
        aps = approaches[phase_name]
        want_right = phase_name.endswith("_R")
        p = 0.0
        for v in vehicles:
            if v["approach"] not in aps:
                continue
            turn = v.get("turn", "through")
            if want_right and turn != "right":
                continue
            if not want_right and turn == "right":
                continue
            w = float(self.weights.get(v["vtype"], 1.0)) if use_weights else 1.0
            p += w * (1.0 if v["halting"] else alpha)
        return p - downstream_penalty

    # Occupancy unit: TraCI getLastStepOccupancy returns a fraction in [0, 1]
    # (lane length occupied / lane length). Upstream phase pressure is in
    # vehicle-count units (Σ w · {1 if halted else α}). Multiplying occupancy by
    # downstream_occupancy_scale (default 10) converts one fully occupied out-edge
    # into ~10 vehicle-equivalents of penalty so the two terms are commensurate.
    # Values outside [0, 1] are clipped; if occupancy is unavailable, fall back to
    # getLastStepVehicleNumber (already in vehicle units, unscaled).
    DOWNSTREAM_OCCUPANCY_SCALE_DEFAULT = 10.0

    def _downstream_count(self, traci_mod, phase_name: str) -> float:
        """Downstream load for max-pressure [vehicle-equivalent].

        Prefer out-edge occupancy ∈ [0, 1], scaled by ``downstream_occupancy_scale``
        (default 10 → vehicle-equivalent). Else raw vehicle count. Never uses
        getLastStepHaltingNumber (that made published maxpressure ≡ queue_pressure).
        """
        occ_scale = float(
            self.params.get(
                "downstream_occupancy_scale",
                self.DOWNSTREAM_OCCUPANCY_SCALE_DEFAULT,
            )
        )
        total = 0.0
        for edge in self.downstream_edges.get(phase_name, []):
            try:
                occ = float(traci_mod.edge.getLastStepOccupancy(edge))
                if occ < 0.0 or occ > 1.0:
                    occ = max(0.0, min(1.0, occ))
                total += occ * occ_scale
                continue
            except Exception:
                pass
            try:
                total += float(traci_mod.edge.getLastStepVehicleNumber(edge))
            except Exception:
                continue
        return total

    def _update_starvation(self, pressures: Dict[str, float], sim_time: float) -> None:
        cur = None
        if not self.state.in_yellow and not self.state.in_all_red:
            cur = self.current_phase_name()
        for p in PHASE_ORDER:
            if p == cur or pressures.get(p, 0.0) <= 0:
                self.state.waiting_since[p] = sim_time
            else:
                self.state.waiting_since.setdefault(p, sim_time)

    def _starved_index(self, pressures: Dict[str, float], sim_time: float) -> Optional[int]:
        cur = self.current_phase_name()
        choice = None
        longest = -1.0
        for i, p in enumerate(PHASE_ORDER):
            if p == cur or pressures.get(p, 0.0) <= 0:
                continue
            waited = sim_time - float(self.state.waiting_since.get(p, sim_time))
            if waited >= self.starvation and waited > longest:
                longest = waited
                choice = i
        return choice

    def _adaptive_step(self, traci_mod, sim_time: float, *, use_weights: bool,
                       use_downstream: bool, hysteresis: float, coord_weight: float = 0.0) -> None:
        self._sim_time = float(sim_time)
        if self._tick_transition():
            self.apply_state(traci_mod)
            return
        D = float(self.params.get("detection_distance_m", self.cfg["controller"]["detection_distance_m"]))
        alpha = float(self.params.get("alpha_moving", self.cfg["controller"]["alpha_moving"]))
        vehicles = self.observe_vehicles(traci_mod, D)
        pressures: Dict[str, float] = {}
        for p in PHASE_ORDER:
            pen = 0.0
            if use_downstream or coord_weight:
                # maxpressure: full downstream weight; coord: neighbor_pressure_weight.
                w = 1.0 if use_downstream else float(coord_weight)
                pen = w * self._downstream_count(traci_mod, p)
            pressures[p] = self.pressure(vehicles, p, use_weights=use_weights, alpha=alpha, downstream_penalty=pen)
        self._update_starvation(pressures, sim_time)
        cur = self.current_phase_name()
        self.state.time_in_phase += 1.0
        starved = self._starved_index(pressures, sim_time)
        # Starvation is independent of max_green. The old branch was unreachable
        # because it required time_in_phase >= starvation (120s) after max_green (90s).
        if starved is not None and self.state.time_in_phase >= self.min_green:
            self._begin_switch(starved, "starvation")
        elif pressures[cur] <= 1e-9 and self.state.time_in_phase >= self.min_green:
            # Prefer a positive-pressure phase. If max-pressure pushed every phase
            # non-positive, still switch to the least-negative alternative.
            positive = [p for p in PHASE_ORDER if p != cur and pressures[p] > 0]
            pool = positive or [p for p in PHASE_ORDER if p != cur]
            if pool:
                best = max(pool, key=lambda p: pressures[p])
                if pressures[best] > pressures[cur]:
                    self._begin_switch(
                        PHASE_ORDER.index(best),
                        "gapout" if positive else "pressure",
                    )
        elif self.state.time_in_phase >= self.max_green:
            best = max(PHASE_ORDER, key=lambda p: pressures[p])
            nxt = PHASE_ORDER.index(best) if best != cur else (self.state.phase_idx + 1) % len(PHASE_ORDER)
            self._begin_switch(nxt, "max_green")
        elif self.state.time_in_phase >= self.min_green:
            best = max(PHASE_ORDER, key=lambda p: pressures[p])
            if best != cur and pressures[best] > pressures[cur] * (1.0 + hysteresis):
                self._begin_switch(PHASE_ORDER.index(best), "pressure")
        self.apply_state(traci_mod)

    def step(self, traci_mod, sim_time: float) -> None:
        raise NotImplementedError

    def normalized_obs(self, traci_mod):
        import numpy as np

        D = float(self.params.get("detection_distance_m", self.cfg["controller"]["detection_distance_m"]))
        vehicles = self.observe_vehicles(traci_mod, D)
        scale_n = 20.0
        scale_w = 40.0
        feats: List[float] = []
        for ap in APPROACHES:
            halt = sum(1 for v in vehicles if v["approach"] == ap and v["halting"])
            move = sum(1 for v in vehicles if v["approach"] == ap and not v["halting"])
            wq = sum(float(self.weights.get(v["vtype"], 1.0)) for v in vehicles if v["approach"] == ap)
            feats.extend([
                min(halt / scale_n, 1.0),
                min(move / scale_n, 1.0),
                min(wq / scale_w, 1.0),
            ])
        one_hot = [0.0, 0.0, 0.0, 0.0]
        one_hot[self.state.phase_idx % 4] = 1.0
        feats.extend(one_hot)
        feats.append(min(self.state.time_in_phase / max(self.max_green, 1.0), 1.0))
        return np.asarray(feats, dtype=np.float32)


class FixedController(BaseController):
    name = "fixed"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.greens = [float(g) for g in self.cfg["signals"]["fixed_greens_s"]]
        self._rebuild_schedule()

    def _rebuild_schedule(self) -> None:
        self._schedule = []
        for i, g in enumerate(self.greens):
            self._schedule.append(("G", i, float(g)))
            self._schedule.append(("Y", i, self.yellow))
            self._schedule.append(("AR", i, self.all_red))
        self._t = 0.0
        self._cursor = 0
        self._left = self._schedule[0][2]

    def step(self, traci_mod, sim_time: float) -> None:
        self._sim_time = float(sim_time)
        self._left -= 1.0
        self._t += 1.0
        if self._left <= 0:
            self._cursor = (self._cursor + 1) % len(self._schedule)
            kind, idx, dur = self._schedule[self._cursor]
            self._left = dur
            self.state.phase_idx = idx
            self.state.in_yellow = kind == "Y"
            self.state.in_all_red = kind == "AR"
            self.state.time_in_phase = 0.0
        else:
            self.state.time_in_phase += 1.0
        self.apply_state(traci_mod)


class FixedTunedController(FixedController):
    """Legacy fixed plan is `fixed`. This plan is chosen on VALIDATION seeds only."""

    name = "fixed_tuned"

    def __init__(self, *args, scenario: str = "balanced", **kwargs):
        super().__init__(*args, **kwargs)
        greens = None
        if self.params and self.params.get("greens"):
            greens = list(self.params["greens"])
        else:
            path = ROOT / "results" / "fixed_tuned.json"
            if not path.exists():
                raise RuntimeError("results/fixed_tuned.json missing; run experiments.fixed_tuned on VALIDATION")
            data = load_json(path)
            greens = list(data["plans"][scenario]["greens"])
        self.greens = [max(self.min_green, float(g)) for g in greens]
        self._rebuild_schedule()


def measured_approach_flows(scenario: str) -> Optional[Dict[str, float]]:
    path = ROOT / "results" / "demand_calibration.json"
    if not path.exists():
        return None
    data = load_json(path)
    flows = (data.get("measured_approach_veh_h") or {}).get(scenario)
    if not flows:
        return None
    return {a: float(flows[a]) for a in APPROACHES if a in flows}


class WebsterController(FixedController):
    name = "webster"

    def __init__(self, *args, scenario: str = "balanced", flows: Optional[Dict[str, float]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        rates = flows or measured_approach_flows(scenario)
        if not rates or any(a not in rates for a in APPROACHES):
            raise RuntimeError(
                "Webster requires VALIDATION-measured approach flows in "
                "results/demand_calibration.json (measured_approach_veh_h). "
                "Refusing config demand to avoid leaking the scenario table."
            )
        s_lane = 1800.0
        y_ns = max(rates["N"], rates["S"]) / (s_lane * 2)
        y_ns_r = 0.3 * y_ns
        y_ew = max(rates["E"], rates["W"]) / (s_lane * 2)
        y_ew_r = 0.3 * y_ew
        Y = min(y_ns + y_ns_r + y_ew + y_ew_r, 0.9)
        L = 4 * (self.yellow + self.all_red)
        C = (1.5 * L + 5) / max(1e-3, (1 - Y))
        C = max(60.0, min(150.0, C))
        greentime = C - L
        shares = [y_ns, y_ns_r, y_ew, y_ew_r]
        ssum = sum(shares) or 1.0
        self.greens = [max(self.min_green, int(greentime * s / ssum)) for s in shares]
        self.flow_source = "validation_measured"
        self._rebuild_schedule()


class ActuatedExternalController(BaseController):
    """SUMO built-in actuated program. Same min/max/yellow/all-red written into tls.add.xml."""

    name = "actuated"

    def step(self, traci_mod, sim_time: float) -> None:
        self._sim_time = float(sim_time)
        try:
            name = traci_mod.trafficlight.getPhaseName(self.tls_id)
            for i, p in enumerate(PHASE_ORDER):
                if name.startswith(p):
                    self.state.phase_idx = i
                    break
        except Exception:
            pass


class QueuePressureController(BaseController):
    """Queue-length pressure. No downstream term."""

    name = "queue_pressure"

    def step(self, traci_mod, sim_time: float) -> None:
        self._adaptive_step(
            traci_mod, sim_time, use_weights=False, use_downstream=False, hysteresis=0.0
        )


class MaxPressureController(BaseController):
    """Max-pressure: incoming queue minus downstream occupancy on out-edges."""

    name = "maxpressure"

    def step(self, traci_mod, sim_time: float) -> None:
        self._adaptive_step(
            traci_mod, sim_time, use_weights=False, use_downstream=True, hysteresis=0.0
        )


class OursCountController(BaseController):
    name = "ours_count"

    def step(self, traci_mod, sim_time: float) -> None:
        hyst = float(self.params.get("hysteresis", self.cfg["controller"]["hysteresis"]))
        self._adaptive_step(
            traci_mod, sim_time, use_weights=False, use_downstream=False, hysteresis=hyst
        )


class OursFuelController(OursCountController):
    name = "XtraFlow"

    def step(self, traci_mod, sim_time: float) -> None:
        hyst = float(self.params.get("hysteresis", self.cfg["controller"]["hysteresis"]))
        self._adaptive_step(
            traci_mod, sim_time, use_weights=True, use_downstream=False, hysteresis=hyst
        )


class OursFuelCoordController(OursFuelController):
    """Fuel pressure minus neighbor_pressure_weight * downstream halting count."""

    name = "XtraFlow_coord"

    def __init__(self, tls_id: str, neighbor_ids: Sequence[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tls_id = tls_id
        self.neighbor_ids = list(neighbor_ids)
        self.w_nb = float(self.cfg["controller"].get("neighbor_pressure_weight", 0.25))

    def step(self, traci_mod, sim_time: float) -> None:
        hyst = float(self.params.get("hysteresis", self.cfg["controller"]["hysteresis"]))
        self._adaptive_step(
            traci_mod, sim_time,
            use_weights=True, use_downstream=False, hysteresis=hyst, coord_weight=self.w_nb,
        )


class RLPPOController(BaseController):
    name = "rl_ppo"
    OBS_DIM = 17

    def __init__(self, model=None, *args, allow_missing_model: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model
        self.allow_missing_model = allow_missing_model

    def set_model(self, model) -> None:
        self.model = model

    def apply_action(self, action: int) -> int:
        """Apply keep (0) or a phase choice (1..4). Return 1 if a switch starts."""
        if self._tick_transition():
            return 0
        self.state.time_in_phase += 1.0
        action = int(action)
        switched = 0
        if self.state.time_in_phase >= self.max_green:
            target = action - 1 if action in (1, 2, 3, 4) else (self.state.phase_idx + 1) % 4
            if target == self.state.phase_idx:
                target = (self.state.phase_idx + 1) % 4
            self._begin_switch(target, "max_green")
            switched = 1
        elif action in (1, 2, 3, 4) and self.state.time_in_phase >= self.min_green:
            target = action - 1
            if target != self.state.phase_idx:
                self._begin_switch(target, "rl")
                switched = 1
        return switched

    def step(self, traci_mod, sim_time: float) -> None:
        self._sim_time = float(sim_time)
        if self._tick_transition():
            self.apply_state(traci_mod)
            return
        if self.model is None:
            if not self.allow_missing_model:
                raise RuntimeError(
                    "rl_ppo model is missing. Train and place results/rl/ppo_best.zip; "
                    "refusing to run the always-keep policy."
                )
            action = 0
        else:
            obs = self.normalized_obs(traci_mod)
            action, _ = self.model.predict(obs, deterministic=True)
            action = int(action)
        self.apply_action(action)
        self.apply_state(traci_mod)

    def _obs(self, traci_mod):
        return self.normalized_obs(traci_mod)


def _load_weights() -> Dict[str, float]:
    path = ROOT / "results" / "weights.json"
    if path.exists():
        d = load_json(path)
        return {k: float(v) for k, v in d.get("w_type", d).items() if isinstance(v, (int, float))}
    return {"two_wheeler": 0.4, "auto_rickshaw": 0.7, "car": 1.0, "bus": 2.5, "truck": 2.0}


def _load_tuned() -> Dict[str, Any]:
    path = ROOT / "results" / "tuned_params.json"
    if path.exists():
        return load_json(path)
    cfg = load_config()
    return {
        "detection_distance_m": cfg["controller"]["detection_distance_m"],
        "alpha_moving": cfg["controller"]["alpha_moving"],
        "hysteresis": cfg["controller"]["hysteresis"],
    }


def _maybe_camera_noise(cfg: dict, noise: Optional[PerceptionNoise]) -> Optional[PerceptionNoise]:
    mode = str(cfg.get("controller", {}).get("info_mode", "oracle"))
    if noise is not None:
        return noise
    if mode == "camera":
        return PerceptionNoise.from_file(ROOT / "perception" / "noise_model.json")
    return None


def make_controller(name: str, cfg=None, scenario: str = "balanced",
                    noise: Optional[PerceptionNoise] = None,
                    weights: Optional[Dict[str, float]] = None,
                    params: Optional[Dict[str, Any]] = None,
                    model=None,
                    tls_id: str = "C",
                    approach_edges: Optional[Dict[str, List[str]]] = None,
                    downstream_edges: Optional[Dict[str, List[str]]] = None,
                    turn_lookup: Optional[Dict[str, str]] = None,
                    groups: Optional[Dict[str, List[int]]] = None,
                    n_links: int = 0,
                    neighbor_ids: Optional[Sequence[str]] = None,
                    allow_missing_model: bool = False) -> BaseController:
    cfg = cfg or load_config()
    weights = weights or _load_weights()
    params = params or _load_tuned()
    noise = _maybe_camera_noise(cfg, noise)
    common = dict(
        cfg=cfg, noise=noise, weights=weights, params=params,
        approach_edges=approach_edges, downstream_edges=downstream_edges,
        turn_lookup=turn_lookup, groups=groups, n_links=n_links,
    )
    if name == "fixed":
        ctrl = FixedController(**common)
    elif name == "fixed_tuned":
        ctrl = FixedTunedController(scenario=scenario, **common)
    elif name == "webster":
        flows = params.get("flows") if isinstance(params, dict) else None
        ctrl = WebsterController(scenario=scenario, flows=flows, **common)
    elif name == "actuated":
        ctrl = ActuatedExternalController(**common)
    elif name == "queue_pressure":
        ctrl = QueuePressureController(**common)
    elif name == "maxpressure":
        ctrl = MaxPressureController(**common)
    elif name == "ours_count":
        ctrl = OursCountController(**common)
    elif name == "XtraFlow":
        ctrl = OursFuelController(**common)
    elif name == "XtraFlow_coord":
        ctrl = OursFuelCoordController(tls_id, neighbor_ids or [], **common)
    elif name == "rl_ppo":
        if model is None and not allow_missing_model:
            zip_path = ROOT / "results" / "rl" / "ppo_best.zip"
            if zip_path.exists():
                from stable_baselines3 import PPO
                model = PPO.load(str(zip_path))
        ctrl = RLPPOController(model=model, allow_missing_model=allow_missing_model, **common)
    else:
        raise ValueError(f"Unknown controller {name}")
    ctrl.tls_id = tls_id
    return ctrl
