"""Offline graph tests with synthetic fallback."""

from __future__ import annotations

from unittest.mock import patch

import networkx as nx
import pytest
from anpr_common.config import Settings
from simulator.graph import (
    SYNTHETIC_GRID_SIZE,
    build_synthetic_grid,
    cache_path,
    enrich_graph_edges,
    load_graph,
    shortest_path,
)


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("CITY_QUERY", "test_city_offline")
    from anpr_common.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    return s


def test_synthetic_grid_shape():
    g = build_synthetic_grid(size=10)
    assert g.number_of_nodes() == 100
    assert g.number_of_edges() > 0
    node = 0
    lat, lon = g.nodes[node]["y"], g.nodes[node]["x"]
    assert 18.0 < lat < 19.0
    assert 73.0 < lon < 74.0


def test_synthetic_grid_has_travel_times():
    g = enrich_graph_edges(build_synthetic_grid(size=5))
    _u, _v, data = next(iter(g.edges(data=True)))
    assert "travel_time_s" in data
    assert data["speed_kmh"] > 0


def test_shortest_path_on_grid():
    g = build_synthetic_grid(size=5)
    g = enrich_graph_edges(g)
    path = shortest_path(g, 0, 24)
    assert path[0] == 0
    assert path[-1] == 24
    assert len(path) >= 2


def test_load_graph_uses_synthetic_when_osm_fails(settings, tmp_path, monkeypatch):
    from simulator import graph as graph_mod

    cache = tmp_path / "cache"
    monkeypatch.setattr(graph_mod, "CACHE_DIR", cache)

    with patch("simulator.graph.download_osm_graph", return_value=None):
        g = load_graph(settings, force_synthetic=False)
    assert isinstance(g, nx.MultiDiGraph)
    assert g.number_of_nodes() == SYNTHETIC_GRID_SIZE**2
    assert cache_path(settings).exists()


def test_force_synthetic_skips_download(settings, tmp_path, monkeypatch):
    from simulator import graph as graph_mod

    cache = tmp_path / "cache"
    monkeypatch.setattr(graph_mod, "CACHE_DIR", cache)

    with patch("simulator.graph.download_osm_graph") as mock_dl:
        g = load_graph(settings, force_synthetic=True)
        mock_dl.assert_not_called()
    assert g.number_of_nodes() == SYNTHETIC_GRID_SIZE**2
