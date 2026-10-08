"""Road graph loading: OSM download with synthetic grid fallback."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np

if TYPE_CHECKING:
    from anpr_common.config import Settings

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
PUNE_CENTER = (18.52, 73.85)
SYNTHETIC_GRID_SIZE = 20

# Default speeds (km/h) by OSM highway type when maxspeed is missing.
ROAD_SPEED_DEFAULTS: dict[str, float] = {
    "motorway": 80.0,
    "trunk": 60.0,
    "primary": 50.0,
    "secondary": 40.0,
    "tertiary": 35.0,
    "residential": 30.0,
    "unclassified": 30.0,
    "service": 20.0,
    "living_street": 20.0,
}


def cache_path(settings: Settings) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if settings.city_query:
        slug = re.sub(r"[^\w]+", "_", settings.city_query.strip().lower())
    elif settings.city_bbox:
        slug = re.sub(r"[^\w]+", "_", settings.city_bbox.strip())
    else:
        slug = "synthetic"
    return CACHE_DIR / f"{slug}.graphml"


def _parse_maxspeed(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).split(";")[0].strip().lower()
    if not text:
        return None
    if text.endswith("mph"):
        try:
            return float(text.replace("mph", "").strip()) * 1.60934
        except ValueError:
            return None
    try:
        return float(text.replace("km/h", "").replace("kph", "").strip())
    except ValueError:
        return None


def edge_speed_kmh(highway: object, maxspeed: object) -> float:
    parsed = _parse_maxspeed(maxspeed)
    if parsed is not None:
        return min(parsed, 120.0)
    if isinstance(highway, list):
        highway = highway[0] if highway else "unclassified"
    return ROAD_SPEED_DEFAULTS.get(str(highway), 30.0)


def enrich_graph_edges(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Attach length_m, speed_kmh, and travel_time_s to every edge."""
    g = graph.copy()
    for u, v, key, data in g.edges(keys=True, data=True):
        length_m = float(data.get("length", 100.0))
        speed = edge_speed_kmh(data.get("highway"), data.get("maxspeed"))
        data["length_m"] = length_m
        data["speed_kmh"] = speed
        data["travel_time_s"] = max(length_m / max(speed, 1.0) * 3.6, 1.0)
    return g


def build_synthetic_grid(
    size: int = SYNTHETIC_GRID_SIZE,
    center: tuple[float, float] = PUNE_CENTER,
    seed: int = 42,
) -> nx.MultiDiGraph:
    """Build a bidirectional grid graph centered near Pune."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = center
    step_lat = 0.004
    step_lon = 0.004

    g = nx.MultiDiGraph()
    g.graph["crs"] = "EPSG:4326"

    for i in range(size):
        for j in range(size):
            node = i * size + j
            lat = lat0 + (i - size / 2) * step_lat
            lon = lon0 + (j - size / 2) * step_lon
            g.add_node(node, x=lon, y=lat)

    for i in range(size):
        for j in range(size):
            u = i * size + j
            if j + 1 < size:
                v = i * size + (j + 1)
                length = 350.0 + rng.uniform(-30, 30)
                speed = 40.0 + rng.uniform(-5, 10)
                attrs = {
                    "length": length,
                    "length_m": length,
                    "highway": "secondary",
                    "speed_kmh": speed,
                    "travel_time_s": length / speed * 3.6,
                }
                g.add_edge(u, v, **attrs)
                g.add_edge(v, u, **attrs)
            if i + 1 < size:
                v = (i + 1) * size + j
                length = 350.0 + rng.uniform(-30, 30)
                speed = 35.0 + rng.uniform(-5, 10)
                attrs = {
                    "length": length,
                    "length_m": length,
                    "highway": "tertiary",
                    "speed_kmh": speed,
                    "travel_time_s": length / speed * 3.6,
                }
                g.add_edge(u, v, **attrs)
                g.add_edge(v, u, **attrs)

    return g


def download_osm_graph(settings: Settings) -> nx.MultiDiGraph | None:
    try:
        import osmnx as ox

        ox.settings.use_cache = True
        ox.settings.log_console = False
        if settings.city_bbox:
            parts = [float(x) for x in settings.city_bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("CITY_BBOX must be west,south,east,north")
            west, south, east, north = parts
            g = ox.graph_from_bbox(bbox=(north, south, east, west), network_type="drive")
        elif settings.city_query:
            g = ox.graph_from_place(settings.city_query, network_type="drive")
        else:
            return None
        g = ox.add_edge_lengths(g)  # type: ignore[attr-defined]
        return enrich_graph_edges(g)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OSM graph download failed (%s); using synthetic grid", exc)
        return None


def load_graph(settings: Settings, force_synthetic: bool = False) -> nx.MultiDiGraph:
    """Load cached GraphML, download OSM, or fall back to synthetic grid."""
    path = cache_path(settings)
    if not force_synthetic and path.exists():
        logger.info("Loading cached graph from %s", path)
        g = nx.read_graphml(path)
        if not isinstance(g, nx.MultiDiGraph):
            g = nx.MultiDiGraph(g)
        return enrich_graph_edges(g)

    osm: nx.MultiDiGraph | None = None
    if not force_synthetic:
        osm = download_osm_graph(settings)
        if osm is not None:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            nx.write_graphml(osm, path)
            logger.info("Cached OSM graph to %s", path)

    if osm is None:
        logger.info(
            "Using synthetic %dx%d grid near Pune", SYNTHETIC_GRID_SIZE, SYNTHETIC_GRID_SIZE
        )
        osm = build_synthetic_grid()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        nx.write_graphml(osm, path)

    return osm


def node_latlon(graph: nx.MultiDiGraph, node: int | str) -> tuple[float, float]:
    data = graph.nodes[node]
    if "y" in data and "x" in data:
        return float(data["y"]), float(data["x"])
    if "lat" in data and "lon" in data:
        return float(data["lat"]), float(data["lon"])
    raise KeyError(f"Node {node} missing coordinates")


def edge_bearing(graph: nx.MultiDiGraph, u: int | str, v: int | str) -> float:
    from anpr_common.geo import bearing_deg

    lat1, lon1 = node_latlon(graph, u)
    lat2, lon2 = node_latlon(graph, v)
    return bearing_deg(lat1, lon1, lat2, lon2)


def path_length_m(graph: nx.MultiDiGraph, path: list[int | str]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        edge_data = graph.get_edge_data(u, v)
        if not edge_data:
            continue
        key = min(edge_data)
        total += float(edge_data[key].get("length_m", edge_data[key].get("length", 100.0)))
    return total


def shortest_path(
    graph: nx.MultiDiGraph,
    origin: int | str,
    destination: int | str,
) -> list[int | str]:
    return nx.shortest_path(graph, origin, destination, weight="travel_time_s")
