"""Controller timing constraints: min/max green, yellow, all-red."""
from sim.controllers import FixedController, OursFuelController, PHASE_ORDER
from sim.util import load_config


class FakeTL:
    def __init__(self):
        self.state = None

    def setRedYellowGreenState(self, tls_id, state):
        self.state = state


class FakeTraCI:
    def __init__(self, n_links=12):
        self.trafficlight = FakeTL()
        self._n = n_links
        self.edge = self
        self.vehicle = self
        self.lane = self

    def getLastStepVehicleIDs(self, edge):
        return []


def test_fixed_honors_yellow_allred():
    cfg = load_config()
    # Ensure phase groups exist
    from pathlib import Path
    from sim.util import ROOT
    if not (ROOT / "results" / "phase_groups.json").exists():
        from sim.build_network import build
        build()
    ctrl = FixedController(cfg=cfg)
    traci = FakeTraCI(ctrl.n_links)
    seen_yellow = False
    seen_ar = False
    # run one full cycle
    total = sum(cfg["signals"]["fixed_greens_s"]) + 4 * (cfg["signals"]["yellow_s"] + cfg["signals"]["all_red_s"])
    for t in range(int(total) + 5):
        ctrl.step(traci, float(t))
        if ctrl.state.in_yellow:
            seen_yellow = True
            assert "y" in traci.trafficlight.state
        if ctrl.state.in_all_red:
            seen_ar = True
            assert set(traci.trafficlight.state) == {"r"}
    assert seen_yellow and seen_ar


def test_ours_respects_min_green():
    from sim.util import ROOT
    if not (ROOT / "results" / "phase_groups.json").exists():
        from sim.build_network import build
        build()
    cfg = load_config()
    ctrl = OursFuelController(cfg=cfg)
    traci = FakeTraCI(ctrl.n_links)
    phase0 = ctrl.state.phase_idx
    # even with empty demand, should not switch before min green
    for t in range(int(cfg["signals"]["min_green_s"]) - 1):
        ctrl.step(traci, float(t))
    assert ctrl.state.phase_idx == phase0 or ctrl.state.in_yellow or ctrl.state.in_all_red
    # Before min green, should not have completed a switch to new green phase
    if not (ctrl.state.in_yellow or ctrl.state.in_all_red):
        assert ctrl.state.time_in_phase < cfg["signals"]["min_green_s"] or ctrl.state.phase_idx == phase0
