"""Network phase correctness and conflict validation."""
from pathlib import Path

import pytest

from sim.util import ROOT


@pytest.fixture(scope="module")
def network():
    net = ROOT / "results" / "networks" / "intersection.net.xml"
    if not net.exists():
        from sim.build_network import build
        build()
    return net


def test_phase_groups_no_conflicts(network):
    from sim.build_network import validate_phase_conflicts
    from sim.util import load_json

    info = load_json(ROOT / "results" / "phase_groups.json")
    report = validate_phase_conflicts(network, info["groups"], info["n_links"])
    assert not report["conflicts"]
    assert len(report["ok"]) >= 1


def test_yellow_allred_in_tllogic():
    tls = ROOT / "results" / "networks" / "tls.add.xml"
    if not tls.exists():
        from sim.build_network import build
        build()
    text = tls.read_text(encoding="utf-8")
    assert 'duration="3"' in text  # yellow
    assert 'duration="2"' in text  # all-red


def test_grid_builds():
    net = ROOT / "results" / "networks" / "grid2x2.net.xml"
    if not net.exists():
        pytest.skip("grid net missing")
    from sim.build_grid import junction_specs
    info = junction_specs(net)
    assert len(info["tls_ids"]) >= 4
    for spec in info["junctions"].values():
        assert spec["validation"]["ok"]
