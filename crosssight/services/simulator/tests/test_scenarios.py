"""Scenario ground-truth tests (offline)."""

from __future__ import annotations

import json

import pytest
from anpr_common.config import Settings
from anpr_common.grammar import normalize_plate
from simulator.cameras import place_cameras
from simulator.graph import build_synthetic_grid, enrich_graph_edges
from simulator.scenarios import build_scenarios, write_scenarios
from simulator.seed import build_zones


@pytest.fixture
def graph():
    return enrich_graph_edges(build_synthetic_grid(size=15))


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("NUM_CAMERAS", "30")
    from anpr_common.config import get_settings

    get_settings.cache_clear()
    return get_settings()


def test_build_scenarios_structure(graph, settings):
    cameras = place_cameras(graph, settings, seed=1)
    zones = build_zones(cameras, graph, settings, seed=1)
    bundle = build_scenarios(cameras, graph, zones, settings, seed=1)

    assert len(bundle.watchlist_plates) == 10
    assert len(bundle.cloned_pairs) >= 1
    assert len(bundle.convoy["plates"]) == 3
    assert len(bundle.convoy["cameras"]) >= 4
    assert bundle.loiterer["passes"] == 6
    assert bundle.wrong_way["reported_direction"] != bundle.wrong_way["allowed_direction"]
    assert bundle.restricted_zone["zone_id"].startswith("restricted")


def test_watchlist_plates_are_valid(graph, settings):
    cameras = place_cameras(graph, settings, seed=2)
    zones = build_zones(cameras, graph, settings, seed=2)
    bundle = build_scenarios(cameras, graph, zones, settings, seed=2)
    for plate in bundle.watchlist_plates:
        result = normalize_plate(plate)
        assert result.valid
        assert result.format in ("standard", "bh")


def test_write_scenarios_json(tmp_path, graph, settings):
    cameras = place_cameras(graph, settings, seed=3)
    zones = build_zones(cameras, graph, settings, seed=3)
    bundle = build_scenarios(cameras, graph, zones, settings, seed=3)
    out = tmp_path / "scenarios.json"
    write_scenarios(bundle, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "watchlist_plates" in data
    assert "expected_alerts" in data
    assert len(data["expected_alerts"]["watchlist"]) == 10
    assert data["expected_alerts"]["geofence"]
