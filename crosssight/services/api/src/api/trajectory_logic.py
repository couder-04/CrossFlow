"""Pure trajectory reconstruction logic (unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import networkx as nx


@dataclass
class Sighting:
    camera_id: str
    ts: datetime
    plate_norm: str
    confidence: float
    vehicle_class: str
    color: str | None
    direction: str | None = None
    crop_key: str | None = None
    lat: float = 0.0
    lng: float = 0.0


@dataclass
class CameraPairInfo:
    camera_a: str
    camera_b: str
    distance_m: float
    adjacent: bool


@dataclass
class Leg:
    from_camera: str
    to_camera: str
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
    observed: bool
    feasible: bool
    eta_s: float
    impossible_hop: bool = False
    path_cameras: list[str] = field(default_factory=list)


def dominant_attribute(values: list[str | None]) -> str | None:
    """Return the most frequent non-null value."""
    counts: dict[str, int] = {}
    for v in values:
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda item: counts[item])


def merge_fuzzy_reads(
    main_reads: list[Sighting],
    candidate_reads_by_plate: dict[str, list[Sighting]],
    *,
    max_cost: float = 1.0,
) -> list[Sighting]:
    """Merge fuzzy candidate reads when vehicle_class and color match the main plate."""
    if not main_reads:
        return []

    main_class = dominant_attribute([r.vehicle_class for r in main_reads])
    main_color = dominant_attribute([r.color for r in main_reads])
    merged = list(main_reads)

    for plate, reads in candidate_reads_by_plate.items():
        if plate == main_reads[0].plate_norm:
            continue
        cand_class = dominant_attribute([r.vehicle_class for r in reads])
        cand_color = dominant_attribute([r.color for r in reads])
        if main_class and cand_class != main_class:
            continue
        if main_color and cand_color != main_color:
            continue
        merged.extend(reads)

    merged.sort(key=lambda r: r.ts)
    return merged


def required_speed_kmh(distance_m: float, delta_s: float) -> float:
    if delta_s <= 0:
        return float("inf")
    return (distance_m / 1000.0) / (delta_s / 3600.0)


def pair_distance(
    camera_a: str,
    camera_b: str,
    pairs: dict[tuple[str, str], CameraPairInfo],
) -> float | None:
    key = (camera_a, camera_b)
    if key in pairs:
        return pairs[key].distance_m
    rev = (camera_b, camera_a)
    if rev in pairs:
        return pairs[rev].distance_m
    return None


def is_adjacent(
    camera_a: str,
    camera_b: str,
    pairs: dict[tuple[str, str], CameraPairInfo],
) -> bool:
    key = (camera_a, camera_b)
    if key in pairs:
        return pairs[key].adjacent
    rev = (camera_b, camera_a)
    if rev in pairs:
        return pairs[rev].adjacent
    return False


def flag_impossible_hops(
    sightings: list[Sighting],
    pairs: dict[tuple[str, str], CameraPairInfo],
    max_speed_kmh: float,
) -> list[bool]:
    """Return per-leg flags aligned with consecutive sightings (len = n-1)."""
    flags: list[bool] = []
    for i in range(len(sightings) - 1):
        a, b = sightings[i], sightings[i + 1]
        if a.camera_id == b.camera_id:
            flags.append(False)
            continue
        dist = pair_distance(a.camera_id, b.camera_id, pairs)
        if dist is None:
            flags.append(True)
            continue
        delta_s = (b.ts - a.ts).total_seconds()
        speed = required_speed_kmh(dist, delta_s)
        flags.append(speed > max_speed_kmh)
    return flags


def build_camera_graph(
    pairs: dict[tuple[str, str], CameraPairInfo],
    travel_times: dict[tuple[str, str], float] | None = None,
    free_flow_speed_kmh: float = 50.0,
) -> nx.DiGraph:
    """Build directed camera graph weighted by travel time (seconds)."""
    g = nx.DiGraph()
    for (a, b), info in pairs.items():
        if travel_times and (a, b) in travel_times:
            weight = travel_times[(a, b)]
        else:
            weight = (info.distance_m / 1000.0) / free_flow_speed_kmh * 3600.0
        g.add_edge(a, b, weight=max(weight, 1.0), distance_m=info.distance_m)
    return g


def shortest_feasible_path(
    camera_a: str,
    camera_b: str,
    available_s: float,
    graph: nx.DiGraph,
) -> tuple[list[str], float] | None:
    """Return camera path and total travel time if feasible within available_s."""
    if camera_a == camera_b:
        return [camera_a], 0.0
    if camera_a not in graph or camera_b not in graph:
        return None
    try:
        path = nx.shortest_path(graph, camera_a, camera_b, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    total = sum(graph[path[i]][path[i + 1]]["weight"] for i in range(len(path) - 1))
    if total > available_s:
        return None
    return path, total


def build_trajectory_legs(
    sightings: list[Sighting],
    pairs: dict[tuple[str, str], CameraPairInfo],
    camera_coords: dict[str, tuple[float, float]],
    *,
    max_speed_kmh: float,
    travel_times: dict[tuple[str, str], float] | None = None,
    free_flow_speed_kmh: float = 50.0,
) -> list[Leg]:
    """Build observed and gap-filled legs between consecutive sightings."""
    if len(sightings) < 2:
        return []

    graph = build_camera_graph(pairs, travel_times, free_flow_speed_kmh)
    hop_flags = flag_impossible_hops(sightings, pairs, max_speed_kmh)
    legs: list[Leg] = []

    for i in range(len(sightings) - 1):
        a, b = sightings[i], sightings[i + 1]
        a_lat, a_lng = camera_coords.get(a.camera_id, (a.lat, a.lng))
        b_lat, b_lng = camera_coords.get(b.camera_id, (b.lat, b.lng))
        delta_s = (b.ts - a.ts).total_seconds()
        impossible = hop_flags[i]

        if a.camera_id == b.camera_id:
            continue

        if is_adjacent(a.camera_id, b.camera_id, pairs):
            legs.append(
                Leg(
                    from_camera=a.camera_id,
                    to_camera=b.camera_id,
                    from_lat=a_lat,
                    from_lng=a_lng,
                    to_lat=b_lat,
                    to_lng=b_lng,
                    observed=True,
                    feasible=not impossible,
                    eta_s=delta_s,
                    impossible_hop=impossible,
                    path_cameras=[a.camera_id, b.camera_id],
                )
            )
            continue

        path_result = shortest_feasible_path(a.camera_id, b.camera_id, delta_s, graph)
        if path_result is None:
            hop = pair_distance(a.camera_id, b.camera_id, pairs)
            eta = delta_s if delta_s > 0 else 0.0
            legs.append(
                Leg(
                    from_camera=a.camera_id,
                    to_camera=b.camera_id,
                    from_lat=a_lat,
                    from_lng=a_lng,
                    to_lat=b_lat,
                    to_lng=b_lng,
                    observed=False,
                    feasible=False,
                    eta_s=eta,
                    impossible_hop=impossible or hop is None,
                    path_cameras=[a.camera_id, b.camera_id],
                )
            )
            continue

        path, total_eta = path_result
        for j in range(len(path) - 1):
            ca, cb = path[j], path[j + 1]
            ca_lat, ca_lng = camera_coords.get(ca, (0.0, 0.0))
            cb_lat, cb_lng = camera_coords.get(cb, (0.0, 0.0))
            edge_eta = graph[ca][cb]["weight"] if graph.has_edge(ca, cb) else total_eta
            legs.append(
                Leg(
                    from_camera=ca,
                    to_camera=cb,
                    from_lat=ca_lat,
                    from_lng=ca_lng,
                    to_lat=cb_lat,
                    to_lng=cb_lng,
                    observed=j == 0 and j + 1 == len(path) - 1 and len(path) == 2,
                    feasible=True,
                    eta_s=edge_eta,
                    impossible_hop=False,
                    path_cameras=path if j == 0 else [],
                )
            )

    return legs


def legs_to_geojson_features(
    sightings: list[Sighting],
    legs: list[Leg],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for s in sightings:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
                "properties": {
                    "kind": "sighting",
                    "ts": s.ts.isoformat(),
                    "camera_id": s.camera_id,
                    "plate_norm": s.plate_norm,
                    "confidence": s.confidence,
                    "vehicle_class": s.vehicle_class,
                    "color": s.color,
                    "direction": s.direction,
                    "crop_key": s.crop_key,
                },
            }
        )
    for leg in legs:
        coords = [[leg.from_lng, leg.from_lat], [leg.to_lng, leg.to_lat]]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "kind": "leg",
                    "from_camera": leg.from_camera,
                    "to_camera": leg.to_camera,
                    "observed": leg.observed,
                    "feasible": leg.feasible,
                    "eta_s": leg.eta_s,
                    "impossible_hop": leg.impossible_hop,
                    "path_cameras": leg.path_cameras,
                },
            }
        )
    return features


def trajectory_summary(
    plate_norm: str,
    sightings: list[Sighting],
    legs: list[Leg],
    pairs: dict[tuple[str, str], CameraPairInfo],
) -> dict[str, Any]:
    cameras = {s.camera_id for s in sightings}
    distance_m = 0.0
    if len(sightings) >= 2:
        for i in range(len(sightings) - 1):
            dist = pair_distance(sightings[i].camera_id, sightings[i + 1].camera_id, pairs)
            if dist is not None:
                distance_m += dist
    duration_s = 0.0
    if len(sightings) >= 2:
        duration_s = (sightings[-1].ts - sightings[0].ts).total_seconds()
    return {
        "plate_norm": plate_norm,
        "distance_m": round(distance_m, 2),
        "duration_s": round(duration_s, 2),
        "camera_count": len(cameras),
        "read_count": len(sightings),
        "impossible_hop_count": sum(1 for leg in legs if leg.impossible_hop),
        "inferred_leg_count": sum(1 for leg in legs if not leg.observed),
    }
