"""Trip scheduling and path traversal with congestion."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import networkx as nx
import numpy as np
from anpr_common.geo import bearing_to_direction

from simulator.cameras import Camera, cameras_by_node
from simulator.graph import edge_bearing, node_latlon, shortest_path
from simulator.vehicles import Vehicle

if TYPE_CHECKING:
    from anpr_common.config import Settings


@dataclass(frozen=True)
class TraversalEvent:
    vehicle: Vehicle
    camera: Camera
    ts: datetime
    direction: str
    speed_kmh: float
    edge_u: int | str
    edge_v: int | str


def demand_multiplier(hour: float) -> float:
    """Time-of-day trip demand curve with morning/evening peaks."""
    morning = np.exp(-0.5 * ((hour - 8.5) / 1.2) ** 2)
    evening = np.exp(-0.5 * ((hour - 18.0) / 1.4) ** 2)
    baseline = 0.25
    return baseline + 0.75 * morning + 0.65 * evening


def congestion_factor(hour: float, edge_betweenness: float) -> float:
    demand = demand_multiplier(hour)
    centrality_boost = 1.0 + min(edge_betweenness * 8.0, 2.5)
    return 1.0 + demand * centrality_boost * 0.35


@dataclass
class TripPlan:
    vehicle: Vehicle
    origin_node: int | str
    dest_node: int | str
    depart_at: datetime
    path: list[int | str]


def zone_nodes(zone_id: str, mapping: dict[str, list[int | str]]) -> list[int | str]:
    return mapping.get(zone_id, [])


def build_zone_node_map(
    graph: nx.MultiDiGraph,
    zone_records: list[dict[str, Any]],
    seed: int = 42,
) -> dict[str, list[int | str]]:
    import h3

    rng = np.random.default_rng(seed)
    nodes = list(graph.nodes())
    mapping: dict[str, list[int | str]] = {}
    for zone in zone_records:
        zid = zone["id"]
        if "h3_cell" in zone:
            cell = zone["h3_cell"]
            lat, lng = h3.cell_to_latlng(
                h3.int_to_str(int(cell)) if isinstance(cell, int) else cell
            )
            # assign nodes nearest centroid
            dists = []
            for n in nodes:
                nlat, nlng = node_latlon(graph, n)
                dists.append((abs(nlat - lat) + abs(nlng - lng), n))
            dists.sort(key=lambda x: x[0])
            mapping[zid] = [n for _, n in dists[: max(3, len(nodes) // 50)]]
        else:
            mapping[zid] = list(rng.choice(nodes, size=min(5, len(nodes)), replace=False))
    return mapping


def schedule_trips(
    graph: nx.MultiDiGraph,
    vehicles: list[Vehicle],
    zone_node_map: dict[str, list[int | str]],
    start: datetime,
    duration: timedelta,
    settings: Settings,
    seed: int = 99,
) -> list[TripPlan]:
    rng = np.random.default_rng(seed)
    plans: list[TripPlan] = []
    end = start + duration
    t = start
    while t < end:
        hour = t.hour + t.minute / 60.0
        if rng.random() > demand_multiplier(hour) * 0.35:
            t += timedelta(minutes=int(rng.integers(3, 12)))
            continue
        vehicle = vehicles[int(rng.integers(0, len(vehicles)))]
        to_work = 7 <= hour <= 10 and rng.random() < 0.7
        to_home = 17 <= hour <= 20 and rng.random() < 0.7
        if to_work:
            origin_zone, dest_zone = vehicle.home_zone, vehicle.work_zone
        elif to_home:
            origin_zone, dest_zone = vehicle.work_zone, vehicle.home_zone
        else:
            zones = list(zone_node_map.keys())
            origin_zone = str(rng.choice(zones))
            dest_zone = str(rng.choice([z for z in zones if z != origin_zone] or zones))

        origin_nodes = zone_nodes(origin_zone, zone_node_map)
        dest_nodes = zone_nodes(dest_zone, zone_node_map)
        if not origin_nodes or not dest_nodes:
            t += timedelta(minutes=5)
            continue
        origin = origin_nodes[int(rng.integers(0, len(origin_nodes)))]
        dest = dest_nodes[int(rng.integers(0, len(dest_nodes)))]
        if origin == dest:
            t += timedelta(minutes=3)
            continue
        try:
            path = shortest_path(graph, origin, dest)
        except nx.NetworkXNoPath:
            t += timedelta(minutes=5)
            continue
        plans.append(
            TripPlan(vehicle=vehicle, origin_node=origin, dest_node=dest, depart_at=t, path=path)
        )
        t += timedelta(minutes=int(rng.integers(2, 8)))
    plans.sort(key=lambda p: p.depart_at)
    return plans


def iter_traversals(
    graph: nx.MultiDiGraph,
    cameras: list[Camera],
    plan: TripPlan,
    edge_bc: dict[tuple[int | str, int | str], float] | None = None,
) -> Iterator[TraversalEvent]:
    """Walk a trip path and yield camera hits with timestamps."""
    cam_map = cameras_by_node(cameras)
    current = plan.depart_at
    path = plan.path
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        edge_data = graph.get_edge_data(u, v) or {}
        key = min(edge_data) if edge_data else 0
        data = edge_data.get(key, {})
        length_m = float(data.get("length_m", data.get("length", 100.0)))
        speed = float(data.get("speed_kmh", 40.0))
        hour = current.hour + current.minute / 60.0
        bc = (edge_bc or {}).get((u, v), 0.0)
        factor = congestion_factor(hour, bc)
        travel_s = max(length_m / max(speed, 1.0) * 3.6 * factor, 1.0)
        bearing = edge_bearing(graph, u, v)
        direction = bearing_to_direction(bearing).value
        speed_kmh = length_m / travel_s * 3.6

        if v in cam_map:
            yield TraversalEvent(
                vehicle=plan.vehicle,
                camera=cam_map[v],
                ts=current + timedelta(seconds=travel_s),
                direction=direction,
                speed_kmh=speed_kmh,
                edge_u=u,
                edge_v=v,
            )
        current += timedelta(seconds=travel_s)


def build_edge_betweenness(graph: nx.MultiDiGraph) -> dict[tuple[int | str, int | str], float]:
    node_bc = nx.betweenness_centrality(graph, weight="length_m", normalized=True)
    out: dict[tuple[int | str, int | str], float] = {}
    for u, v, _k, _d in graph.edges(keys=True, data=True):
        out[(u, v)] = max(node_bc.get(u, 0.0), node_bc.get(v, 0.0))
    return out
