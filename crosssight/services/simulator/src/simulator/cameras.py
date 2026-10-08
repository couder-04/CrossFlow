"""Camera placement on high-betweenness graph nodes."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np
from anpr_common.geo import bearing_to_direction

from simulator.graph import edge_bearing, node_latlon

if TYPE_CHECKING:
    from anpr_common.config import Settings


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    lat: float
    lng: float
    heading_deg: float
    lanes: int
    allowed_direction: str
    osm_u: int
    osm_v: int
    node_id: int | str
    betweenness: float = 0.0

    def to_db_row(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lng": self.lng,
            "heading_deg": self.heading_deg,
            "lanes": self.lanes,
            "allowed_direction": self.allowed_direction,
            "osm_u": self.osm_u,
            "osm_v": self.osm_v,
            "status": "active",
        }


def _pick_out_edge(
    graph: nx.MultiDiGraph, node: int | str, rng: np.random.Generator
) -> tuple[int | str, int | str]:
    successors = list(graph.successors(node))
    if not successors:
        predecessors = list(graph.predecessors(node))
        if not predecessors:
            return node, node
        v = int(rng.choice(predecessors))
        return v, node
    v = int(rng.choice(successors))
    return node, v


def place_cameras(
    graph: nx.MultiDiGraph,
    settings: Settings,
    seed: int = 42,
) -> list[Camera]:
    """Place cameras at intersections with highest betweenness centrality."""
    rng = np.random.default_rng(seed)
    g = graph.copy()
    if not nx.is_directed(g):
        g = nx.MultiDiGraph(g)

    bc = nx.betweenness_centrality(g, weight="length_m", normalized=True)
    ranked = sorted(bc.items(), key=lambda item: item[1], reverse=True)
    n = min(settings.num_cameras, len(ranked))
    selected = ranked[:n]

    cameras: list[Camera] = []
    for idx, (node, score) in enumerate(selected):
        lat, lng = node_latlon(g, node)
        u, v = _pick_out_edge(g, node, rng)
        heading = edge_bearing(g, u, v) if u != v else float(rng.uniform(0, 360))
        direction = bearing_to_direction(heading).value
        lanes = int(rng.integers(2, 5))
        if score > 0.05:
            lanes = min(lanes + 1, 6)
        cam_id = f"cam-{idx + 1:03d}"
        cameras.append(
            Camera(
                id=cam_id,
                name=f"Camera {idx + 1}",
                lat=lat,
                lng=lng,
                heading_deg=heading,
                lanes=lanes,
                allowed_direction=direction,
                osm_u=int(u),
                osm_v=int(v),
                node_id=node,
                betweenness=float(score),
            )
        )
    return cameras


def cameras_by_node(cameras: list[Camera]) -> dict[int | str, Camera]:
    return {c.node_id: c for c in cameras}


def compute_camera_pairs(
    graph: nx.MultiDiGraph,
    cameras: list[Camera],
) -> list[dict[str, object]]:
    """Precompute network distance and adjacency for every camera pair."""
    node_to_cam = cameras_by_node(cameras)
    cam_nodes = {c.id: c.node_id for c in cameras}
    pairs: list[dict[str, object]] = []

    for i, cam_a in enumerate(cameras):
        for cam_b in cameras[i + 1 :]:
            try:
                path = nx.shortest_path(
                    graph,
                    cam_nodes[cam_a.id],
                    cam_nodes[cam_b.id],
                    weight="length_m",
                )
            except nx.NetworkXNoPath:
                continue
            dist = 0.0
            for u, v in pairwise(path):
                edge_data = graph.get_edge_data(u, v) or {}
                key = min(edge_data) if edge_data else 0
                dist += float(edge_data[key].get("length_m", edge_data[key].get("length", 100.0)))

            interior = [n for n in path[1:-1] if n in node_to_cam]
            adjacent = len(interior) == 0
            pairs.append(
                {
                    "camera_a": cam_a.id,
                    "camera_b": cam_b.id,
                    "distance_m": dist,
                    "adjacent": adjacent,
                }
            )
            pairs.append(
                {
                    "camera_a": cam_b.id,
                    "camera_b": cam_a.id,
                    "distance_m": dist,
                    "adjacent": adjacent,
                }
            )
    return pairs
