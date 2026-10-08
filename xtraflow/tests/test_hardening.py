"""Correctness tests for noise, signals, SSM, grid mapping, and replay."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from sim.controllers import (
    PHASE_ORDER,
    MaxPressureController,
    OursFuelController,
    OursFuelCoordController,
    PerceptionNoise,
    QueuePressureController,
    RLPPOController,
)
from sim.metrics import conflicts_per_1000, fuel_per_departed, parse_ssm_conflicts, parse_ssm_details, parse_tripinfo
from sim.util import ROOT, load_config


def _groups():
    path = ROOT / "results" / "phase_groups.json"
    if not path.exists():
        pytest.skip("phase groups not built")
    from sim.util import load_json
    return load_json(path)


class _TL:
    def __init__(self):
        self.state = None
        self.program = None

    def setRedYellowGreenState(self, tls_id, state):
        self.state = state

    def setProgram(self, tls_id, program):
        self.program = program

    def getPhaseName(self, tls_id):
        return "NS_TL"


class World:
    """Fake TraCI with a fixed set of vehicles on one edge."""

    def __init__(self, edge_ids=None, n=0, approach_edge="N_in", halting_edges=None,
                 occupancy_edges=None):
        self.trafficlight = _TL()
        self.edge = self
        self.vehicle = self
        self.lane = self
        self.ids = [f"v{i}" for i in range(n)]
        self.edge_ids = {approach_edge: list(self.ids)}
        if edge_ids:
            self.edge_ids.update(edge_ids)
        self.halt = dict(halting_edges or {})
        self.occ = dict(occupancy_edges or {})
        self._lane_len = 100.0

    def getLastStepVehicleIDs(self, edge):
        return list(self.edge_ids.get(edge, []))

    def getLastStepHaltingNumber(self, edge):
        return float(self.halt.get(edge, 0))

    def getLastStepVehicleNumber(self, edge):
        if edge in self.occ:
            return float(self.occ[edge]) * 10.0
        return float(self.halt.get(edge, 0))

    def getLastStepOccupancy(self, edge):
        if edge in self.occ:
            return float(self.occ[edge])
        # Force vehicle-number fallback when only halt counts were provided
        # (coord / legacy tests).
        raise AttributeError(f"no occupancy for {edge}")

    def getLaneID(self, vid):
        return "N_in_1"

    def getLanePosition(self, vid):
        return 90.0

    def getLength(self, lane_id):
        return self._lane_len

    def getSpeed(self, vid):
        return 0.0

    def getTypeID(self, vid):
        return "car"

    def getRoute(self, vid):
        return ["N_in", "S_out"]


def _ctrl(noise=None, **kwargs):
    info = _groups()
    cfg = load_config()
    return OursFuelController(
        cfg=cfg, groups=info["groups"], n_links=info["n_links"], noise=noise, **kwargs,
    )


def test_noise_fraction_persistence_and_seeds():
    info = _groups()
    cfg = load_config()
    noise = PerceptionNoise(detect_miss_rate=0.3, mean_burst_s=3.0)
    world = World(n=30)

    def run(seed):
        ctrl = OursFuelController(cfg=cfg, groups=info["groups"], n_links=info["n_links"], noise=noise)
        ctrl.set_noise_rng(random.Random(seed))
        series = []
        for _ in range(400):
            seen = {v["id"] for v in ctrl.observe_vehicles(world, 100)}
            series.append(seen)
        return series

    a = run("balanced-XtraFlow-1-noise")
    b = run("balanced-XtraFlow-1-noise")
    c = run("balanced-XtraFlow-2-noise")
    assert a == b
    assert a != c
    flat = [vid for step in a for vid in step]
    visible_frac = len(flat) / (400 * 30)
    assert abs(visible_frac - 0.7) < 0.05
    for i in range(30):
        vid = f"v{i}"
        states = {vid in step for step in a}
        assert states == {True, False}


def test_noise_refuses_default_rng():
    ctrl = _ctrl(noise=PerceptionNoise(detect_miss_rate=0.3))
    with pytest.raises(RuntimeError):
        ctrl.observe_vehicles(World(n=1), 100)


def test_observe_vehicles_fake_traci():
    ctrl = _ctrl()
    seen = ctrl.observe_vehicles(World(n=2), 100)
    assert len(seen) == 2
    assert {v["approach"] for v in seen} == {"N"}
    assert {v["turn"] for v in seen} == {"through"}


def test_min_green_with_competing_demand():
    info = _groups()
    cfg = load_config()
    ctrl = OursFuelController(cfg=cfg, groups=info["groups"], n_links=info["n_links"])
    # Demand on the east approach, which competes with the opening NS phase.
    world = World(n=8, approach_edge="E_in")
    world.getLaneID = lambda vid: "E_in_1"
    world.getRoute = lambda vid: ["E_in", "W_out"]
    phase0 = ctrl.state.phase_idx
    for t in range(int(cfg["signals"]["min_green_s"]) - 1):
        ctrl.step(world, float(t))
    assert ctrl.state.phase_idx == phase0
    assert not ctrl.state.in_yellow


def test_yellow_allred_when_switch_has_demand():
    info = _groups()
    cfg = load_config()
    cfg = {**cfg, "signals": {**cfg["signals"], "min_green_s": 2, "max_green_s": 30, "starvation_s": 4}}
    ctrl = OursFuelController(cfg=cfg, groups=info["groups"], n_links=info["n_links"])
    world = World(n=6, approach_edge="E_in")
    world.getLaneID = lambda vid: "E_in_1"
    world.getRoute = lambda vid: ["E_in", "W_out"]
    saw_yellow = False
    saw_red = False
    for t in range(20):
        ctrl.step(world, float(t))
        if ctrl.state.in_yellow:
            saw_yellow = True
            assert "y" in world.trafficlight.state
        if ctrl.state.in_all_red:
            saw_red = True
            assert set(world.trafficlight.state) == {"r"}
    assert saw_yellow and saw_red


def test_starvation_serves_waiting_phase_before_max_green():
    """A side street with light demand must be forced on before max green.

    The main street still has a queue, so this is not a gap-out, and the side
    street is not the pressure winner, so this is not a pressure switch.
    """
    info = _groups()
    cfg = load_config()
    cfg = {**cfg, "signals": {**cfg["signals"], "min_green_s": 2, "max_green_s": 40, "starvation_s": 5}}
    ctrl = OursFuelController(cfg=cfg, groups=info["groups"], n_links=info["n_links"])
    world = World(n=0, edge_ids={"N_in": [f"n{i}" for i in range(8)], "E_in": ["e0"]})

    def lane(vid):
        return "E_in_1" if str(vid).startswith("e") else "N_in_1"

    def route(vid):
        return ["E_in", "W_out"] if str(vid).startswith("e") else ["N_in", "S_out"]

    world.getLaneID = lane
    world.getRoute = route
    switched_to = None
    for t in range(15):
        ctrl.step(world, float(t))
        if ctrl.state.last_switch_reason == "starvation":
            switched_to = getattr(ctrl.state, "_pending_idx", None)
            break
    assert switched_to == PHASE_ORDER.index("EW_TL")
    assert ctrl.state.time_in_phase < cfg["signals"]["max_green_s"]


def test_coord_differs_from_independent_when_downstream_is_full():
    info = _groups()
    cfg = load_config()
    cfg = {**cfg, "signals": {**cfg["signals"], "min_green_s": 1, "max_green_s": 90, "starvation_s": 1000}}
    world = World(n=12, halting_edges={"S_out": 500, "N_out": 500, "E_out": 500, "W_out": 500})
    indep = OursFuelController(cfg=cfg, groups=info["groups"], n_links=info["n_links"])
    coord = OursFuelCoordController("C", ["J01"], cfg=cfg, groups=info["groups"], n_links=info["n_links"])
    indep.state.phase_idx = PHASE_ORDER.index("EW_TL")
    coord.state.phase_idx = PHASE_ORDER.index("EW_TL")
    indep.state.time_in_phase = 5
    coord.state.time_in_phase = 5
    indep.step(world, 10)
    coord.step(world, 10)
    assert indep.state.last_switch_reason in ("pressure", "gapout", "starvation", "max_green")
    assert getattr(indep.state, "_pending_idx", None) == PHASE_ORDER.index("NS_TL")
    assert getattr(coord.state, "_pending_idx", None) != PHASE_ORDER.index("NS_TL")


def test_downstream_penalty_nonzero_when_out_edges_occupied():
    """Occupancy on out-edges must produce a non-zero max-pressure penalty."""
    info = _groups()
    cfg = load_config()
    mp = MaxPressureController(
        cfg=cfg, groups=info["groups"], n_links=info["n_links"],
        downstream_edges=info["downstream_edges"],
    )
    empty = World(n=0)
    assert mp._downstream_count(empty, "NS_TL") == 0.0
    busy = World(n=0, occupancy_edges={"S_out": 0.4, "N_out": 0.3, "E_out": 0.2, "W_out": 0.1})
    pen = mp._downstream_count(busy, "NS_TL")
    assert pen > 0.0
    # Default scale 10 → 0.4+0.3+0.2+0.1 = 1.0 occupancy → 10.0 vehicle-eq.
    assert pen == pytest.approx(10.0)
    # Right phase only sees E_out + W_out in the published mapping.
    assert mp._downstream_count(busy, "NS_R") == pytest.approx(3.0)


def test_downstream_occupancy_unit_is_fraction_scaled_to_vehicle_eq():
    """Occupancy is TraCI [0,1]; penalty = clip(occ) * downstream_occupancy_scale.

    Static check of the max-pressure unit bridge: upstream pressure is vehicle
    counts, so a full out-edge (occ=1) contributes exactly ``scale`` vehicle-eq.
    Out-of-range occupancy is clipped into [0, 1].
    """
    info = _groups()
    cfg = load_config()
    scale = 7.5
    mp = MaxPressureController(
        cfg=cfg, groups=info["groups"], n_links=info["n_links"],
        downstream_edges={"NS_TL": ["S_out"], "NS_R": [], "EW_TL": [], "EW_R": []},
        params={"downstream_occupancy_scale": scale},
    )
    assert MaxPressureController.DOWNSTREAM_OCCUPANCY_SCALE_DEFAULT == 10.0

    for occ, expected in [
        (0.0, 0.0),
        (0.5, 0.5 * scale),
        (1.0, 1.0 * scale),
        (-0.25, 0.0),       # clipped
        (1.5, 1.0 * scale),  # clipped
    ]:
        world = World(n=0, occupancy_edges={"S_out": occ})
        assert mp._downstream_count(world, "NS_TL") == pytest.approx(expected)

    # Fallback path: when occupancy API is missing, use vehicle count unscaled.
    class _NoOcc(World):
        def getLastStepOccupancy(self, edge):
            raise RuntimeError("occupancy unavailable")

    fb = _NoOcc(n=0, halting_edges={"S_out": 4})
    assert mp._downstream_count(fb, "NS_TL") == pytest.approx(4.0)


def test_weight_sensitivity_variants_do_not_cancel():
    from experiments.weight_sensitivity import (
        STRETCHES,
        _base_weights,
        _variant,
        variants_are_distinct,
    )
    base = _base_weights()
    assert variants_are_distinct(base) == []
    # Uniform multiply + /car would cancel; stretch must change ratios.
    cal = _variant("calibrated_stretch", 1.0, base)
    half = _variant("calibrated_stretch", 0.5, base)
    hot = _variant("calibrated_stretch", 2.0, base)
    flat = _variant("equal", 1.0, base)
    assert all(v == 1.0 for v in flat.values())
    assert cal["two_wheeler"] != hot["two_wheeler"]
    assert cal["truck"] != half["truck"]
    assert half["truck"] != flat["truck"]
    # Explicit stretch=0 matches equal (not scheduled, but API contract).
    assert _variant("calibrated_stretch", 0.0, base) == flat
    # auto_as_truck must differ from calibrated (auto==car under the proxy).
    stress = _variant("auto_as_truck", 1.0, base)
    assert stress["auto_rickshaw"] == pytest.approx(cal["truck"])
    assert stress["auto_rickshaw"] != pytest.approx(cal["auto_rickshaw"])
    # Every stretch in the study schedule is unique.
    sigs = {
        tuple(round(_variant("calibrated_stretch", s, base)[k], 8) for k in sorted(base))
        for s in STRETCHES
    }
    assert len(sigs) == len(STRETCHES)


def test_mix_override_tag_is_process_stable():
    """Mix tripinfo tags must not use salted builtin hash()."""
    import hashlib
    from sim.run_sim import run_one
    import inspect
    src = inspect.getsource(run_one)
    assert "hash(mix_key)" not in src
    assert "hashlib.md5" in src
    mix = {"car": 0.3, "two_wheeler": 0.4, "auto_rickshaw": 0.1, "bus": 0.05, "truck": 0.15}
    mix_key = ",".join(f"{k}={float(mix[k]):.4f}" for k in sorted(mix))
    a = hashlib.md5(mix_key.encode("utf-8")).hexdigest()[:8]
    b = hashlib.md5(mix_key.encode("utf-8")).hexdigest()[:8]
    assert a == b
    assert len(a) == 8


def test_maxpressure_differs_from_queue_when_downstream_occupied():
    """Occupancy on out-edges must change phase choice vs queue_pressure."""
    info = _groups()
    cfg = load_config()
    cfg = {**cfg, "signals": {**cfg["signals"], "min_green_s": 1, "max_green_s": 90, "starvation_s": 1000}}
    # Heavy NS demand; fill NS_TL out-edges so max-pressure drops NS_TL below EW_TL
    # while queue_pressure still prefers NS_TL.
    edge_ids = {
        "N_in": [f"n{i}" for i in range(6)],
        "S_in": [f"s{i}" for i in range(6)],
        "E_in": [f"e{i}" for i in range(3)],
        "W_in": [f"w{i}" for i in range(3)],
    }
    # Occupancy on all outs; TL phases pay for four edges, R phases for two.
    world = World(
        n=0,
        edge_ids=edge_ids,
        occupancy_edges={"S_out": 1.0, "N_out": 1.0, "E_out": 1.0, "W_out": 1.0},
    )

    def lane(vid):
        if str(vid).startswith("n"):
            return "N_in_1"
        if str(vid).startswith("s"):
            return "S_in_1"
        if str(vid).startswith("e"):
            return "E_in_1"
        return "W_in_1"

    def route(vid):
        if str(vid).startswith("n"):
            return ["N_in", "S_out"]
        if str(vid).startswith("s"):
            return ["S_in", "N_out"]
        if str(vid).startswith("e"):
            return ["E_in", "W_out"]
        return ["W_in", "E_out"]

    world.getLaneID = lane
    world.getRoute = route

    qp = QueuePressureController(
        cfg=cfg, groups=info["groups"], n_links=info["n_links"],
        downstream_edges=info["downstream_edges"],
    )
    mp = MaxPressureController(
        cfg=cfg, groups=info["groups"], n_links=info["n_links"],
        downstream_edges=info["downstream_edges"],
    )
    for ctrl in (qp, mp):
        ctrl.state.phase_idx = PHASE_ORDER.index("EW_TL")
        ctrl.state.time_in_phase = 5.0
        ctrl.step(world, 10.0)

    qp_next = getattr(qp.state, "_pending_idx", None)
    mp_next = getattr(mp.state, "_pending_idx", None)
    # Queue pressure sees raw NS demand (12) > EW (6) and switches to NS_TL.
    assert qp_next == PHASE_ORDER.index("NS_TL")
    # Max-pressure: TL phases pay occupancy on all four outs; R phases only two,
    # so NS_R can beat NS_TL even though upstream NS demand is higher.
    assert mp_next is None or mp_next != PHASE_ORDER.index("NS_TL")
    assert qp_next != mp_next


def test_ssm_demo_files_and_fixture(tmp_path: Path):
    fixed = parse_ssm_conflicts(ROOT / "results" / "demo" / "demo_fixed_seed1.ssm.xml")
    ours = parse_ssm_conflicts(ROOT / "results" / "demo" / "demo_XtraFlow_seed1.ssm.xml")
    assert fixed == 160
    assert ours == 128
    xml = """<ssm>
      <conflict><minTTC value="0.4"/><PET value="1.2"/></conflict>
      <conflict><minTTC value="2.0"/></conflict>
      <conflict><minTTC value="0.9"/><DRAC value="3.0"/></conflict>
    </ssm>"""
    path = tmp_path / "hand.ssm.xml"
    path.write_text(xml, encoding="utf-8")
    assert parse_ssm_conflicts(path) == 2
    details = parse_ssm_details(path)
    assert details["pet"] == [1.2]
    assert details["drac"] == [3.0]
    assert conflicts_per_1000(2, 100) == pytest.approx(20.0)


def test_unfinished_trips_count_in_fuel(tmp_path: Path):
    xml = """<tripinfos>
      <tripinfo id="a" vType="car" arrival="10" waitingTime="1" timeLoss="1" duration="10" waitingCount="0">
        <emissions fuel_abs="740000" CO2_abs="0"/>
      </tripinfo>
      <tripinfo id="b" vType="car" arrival="-1.00" waitingTime="5" timeLoss="5" duration="20" waitingCount="1">
        <emissions fuel_abs="740000" CO2_abs="0"/>
      </tripinfo>
    </tripinfos>"""
    path = tmp_path / "trip.xml"
    path.write_text(xml, encoding="utf-8")
    m = parse_tripinfo(path)
    assert m["n_completed"] == 1
    assert m["n_unfinished"] == 1
    assert m["total_fuel_L"] == pytest.approx(2.0)
    assert fuel_per_departed(m["total_fuel_L"], 2) == pytest.approx(1.0)
    # Averaging only completed trips would report 1 L and hide the second vehicle.
    assert m["fuel_per_vehicle_L"] == pytest.approx(1.0)


def test_grid_edges_are_not_the_single_intersection_names():
    net = ROOT / "results" / "networks" / "grid2x2.net.xml"
    if not net.exists():
        pytest.skip("grid net missing")
    from sim.build_grid import junction_specs
    info = junction_specs(net)
    assert len(info["tls_ids"]) >= 4
    for spec in info["junctions"].values():
        edges = [e for group in spec["approach_edges"].values() for e in group]
        assert edges
        assert not any(e in {"N_in", "S_in", "E_in", "W_in"} for e in edges)
        assert not spec["validation"]["conflicts"]


def test_replay_decision_without_sumo():
    from perception.replay import replay_decision
    counts = {
        "N": {"car": 6, "halting_est": 6},
        "S": {"car": 0, "halting_est": 0},
        "E": {"car": 0, "halting_est": 0},
        "W": {"car": 0, "halting_est": 0},
    }
    decision = replay_decision(counts)
    assert decision["next_phase"] == "NS_TL"
    assert decision["n_vehicles"] == 6


def test_halted_from_displacement():
    from perception.yolo_counts import halted_from_track
    still = [(0.5, 0.5)] * 6
    moving = [(0.1 + 0.05 * i, 0.5) for i in range(6)]
    assert halted_from_track(still)
    assert not halted_from_track(moving)


def test_rl_missing_model_raises():
    info = _groups()
    ctrl = RLPPOController(model=None, groups=info["groups"], n_links=info["n_links"])
    with pytest.raises(RuntimeError):
        ctrl.step(None, 0.0)


def test_config_lock_uses_tmp(tmp_path: Path):
    from sim.util import assert_config_locked, freeze_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    lock = tmp_path / "config.lock"
    freeze_config(cfg, lock)
    text = lock.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "config.yaml" in text
    assert_config_locked(cfg, lock)


def test_evaluate_labels_empirical_only_when_paired(tmp_path: Path):
    from perception.evaluate_detector import evaluate_from_labels
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "clip.json").write_text(
        '{"counts": {"car": 10, "bus": 2}, "detections": {"car": 7, "bus": 2}}',
        encoding="utf-8",
    )
    out = tmp_path / "noise.json"
    result = evaluate_from_labels(tmp_path, labels, out_path=out)
    assert result["source"] == "empirical"
    assert result["detect_miss_rate"] == pytest.approx(1 - 9 / 12)
    from sim.util import load_json
    assert load_json(ROOT / "perception" / "noise_model.json")["source"] == "assumed"


@pytest.mark.skipif(not (ROOT / "results" / "networks" / "intersection.net.xml").exists(), reason="net missing")
@pytest.mark.parametrize("name", ["fixed", "actuated", "queue_pressure", "maxpressure", "ours_count", "XtraFlow"])
def test_sumo_smoke_60s(name):
    from sim.util import locate_sumo, tool_cmd
    try:
        locate_sumo()
        tool_cmd("sumo")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SUMO missing: {exc}")
    from sim.run_sim import run_one
    row = run_one("low_demand", name, seed=1, horizon_s=60, run_id="pytest60")
    assert row["status"] in {"ok", "timeout", "gridlock"}
    assert row["error"] == ""
    assert "n_unfinished" in row
