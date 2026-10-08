"""approach_veh_h is demand: it must not depend on the controller or on the drain period."""
import copy

import pytest

from sim.util import ROOT, load_config

SEED = 910001
HORIZON = 150

pytestmark = pytest.mark.skipif(
    not (ROOT / "results" / "networks" / "intersection.net.xml").exists(), reason="net missing"
)


@pytest.fixture
def short_cfg():
    cfg = copy.deepcopy(load_config())
    cfg["simulation"]["demand_horizon_s"] = HORIZON
    yield cfg
    for suffix in (".rou.xml", ".rou.meta.json"):
        (ROOT / "results" / "demand" / f"balanced_seed{SEED}{suffix}").unlink(missing_ok=True)


def _run(controller: str, cfg: dict) -> dict:
    from sim.util import locate_sumo, tool_cmd

    try:
        locate_sumo()
        tool_cmd("sumo")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SUMO missing: {exc}")
    from sim.run_sim import run_one

    return run_one("balanced", controller, SEED, cfg=cfg, run_id="pytest_demand")


def test_same_seed_same_approach_rate_under_two_controllers(short_cfg):
    fixed = _run("fixed", short_cfg)
    actuated = _run("actuated", short_cfg)
    assert fixed["approach_veh_h"] == actuated["approach_veh_h"]
    assert sum(fixed["approach_veh_h"].values()) > 0


def test_rate_is_per_hour_of_demand_not_of_simulated_time(short_cfg):
    row = _run("fixed", short_cfg)
    counts = {a: v * HORIZON / 3600.0 for a, v in row["approach_veh_h"].items()}
    # Every counted vehicle departed inside the demand horizon, so the totals are integers
    # bounded by the number of departures (vehicles never seen on an approach are excluded).
    for count in counts.values():
        assert count == pytest.approx(round(count))
    assert round(sum(counts.values())) <= row["n_departed"]
