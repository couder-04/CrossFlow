"""Offline city simulation -> camera flow, with no databases or message bus.

This drives CrossSight's own simulator (synthetic road grid, camera placement,
vehicle fleet, trip scheduling, camera traversals) and aggregates what the cameras
would have counted into ``FlowWindow``-shaped records, one per camera, arm and
5-minute window. It is the same data CrossSight's ``flow_5min`` table holds, except
that quiet windows are kept as explicit zeros.

Arm convention (XtraFlow): the side traffic arrives from, i.e. the opposite of the
travel direction. A vehicle travelling north arrives from the south arm. Diagonal
travel directions (NE, SW, ...) fit no arm and are counted as ``dropped`` rather than
guessed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

WINDOW_S = 300

# travel direction -> arm the vehicle arrived from
ARRIVES_FROM = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass
class CityFlow:
    """Everything the offline city produced for one simulated day."""

    records: list[dict[str, Any]]  # FlowWindow-shaped, with an extra ``approach``
    start: datetime
    hours: int
    num_cameras: int
    num_vehicles: int
    streams: int = 1
    traversals: int = 0
    dropped_diagonal: int = 0
    by_camera_hour: dict[tuple[str, int], dict[str, int]] = field(default_factory=dict)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _ensure_crosssight_on_path() -> None:
    """Make CrossSight's source packages importable when running from a plain checkout."""
    import sys

    from .paths import repo_root

    base = repo_root() / "crosssight"
    for rel in ("packages/anpr_common/src", "services/simulator/src"):
        p = str(base / rel)
        if p not in sys.path:
            sys.path.append(p)


def simulate_city(
    *,
    num_vehicles: int = 4000,
    num_cameras: int = 60,
    hours: int = 24,
    seed: int = 42,
    start: datetime | None = None,
    streams: int = 1,
) -> CityFlow:
    """Run CrossSight's simulator offline and aggregate camera counts.

    ``streams`` is the number of independent demand streams to superimpose. CrossSight's
    scheduler starts one trip every 2-8 minutes, which is enough for plate-read demos
    (about 150 trips a day) but nowhere near junction-scale traffic. Each stream is one
    full ``schedule_trips`` run with its own seed, so ``streams=N`` is N times the trip
    rate with the same road network, cameras, routing and daily demand curve. The value
    is recorded on the result and shown in every report.
    """
    _ensure_crosssight_on_path()
    # Imported here so the bridge and signals modules stay light.
    from anpr_common.config import Settings
    from simulator.cameras import place_cameras
    from simulator.graph import build_synthetic_grid
    from simulator.seed import build_zones
    from simulator.trips import (
        build_edge_betweenness,
        build_zone_node_map,
        iter_traversals,
        schedule_trips,
    )
    from simulator.vehicles import generate_fleet

    settings = Settings(NUM_VEHICLES=num_vehicles, NUM_CAMERAS=num_cameras)
    graph = build_synthetic_grid(seed=seed)
    cameras = place_cameras(graph, settings, seed=seed)
    zones = build_zones(cameras, graph, settings)
    zone_ids = [z["id"] for z in zones] or ["ward-001"]
    zone_node_map = build_zone_node_map(graph, zones)
    vehicles = generate_fleet(settings, zone_ids, seed=seed)

    t0 = (start or datetime(2026, 1, 12, tzinfo=UTC)).astimezone(UTC)
    t0 = t0.replace(hour=0, minute=0, second=0, microsecond=0)
    plans = []
    for i in range(max(1, streams)):
        plans += schedule_trips(
            graph, vehicles, zone_node_map, t0, timedelta(hours=hours), settings, seed=seed + i
        )
    edge_bc = build_edge_betweenness(graph)

    # (camera, arm, window index) -> {class: n}
    counts: dict[tuple[str, str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    traversals = 0
    dropped = 0
    for plan in plans:
        for ev in iter_traversals(graph, cameras, plan, edge_bc):
            traversals += 1
            arm = ARRIVES_FROM.get(ev.direction)
            if arm is None:
                dropped += 1
                continue
            idx = int((ev.ts - t0).total_seconds() // WINDOW_S)
            if idx < 0 or idx >= hours * 3600 // WINDOW_S:
                continue
            counts[(ev.camera.id, arm, idx)][ev.vehicle.vehicle_class.value] += 1

    flow = CityFlow(
        records=[],
        start=t0,
        hours=hours,
        num_cameras=len(cameras),
        num_vehicles=len(vehicles),
        streams=max(1, streams),
        traversals=traversals,
        dropped_diagonal=dropped,
    )

    # Which (camera, arm) pairs ever saw traffic; each gets every window, zeros included.
    sources = sorted({(c, a) for (c, a, _i) in counts})
    n_windows = hours * 3600 // WINDOW_S
    for cam, arm in sources:
        for idx in range(n_windows):
            by_class = dict(counts.get((cam, arm, idx), {}))
            ws = t0 + timedelta(seconds=idx * WINDOW_S)
            flow.records.append(
                {
                    "camera_id": cam,
                    "approach": arm,
                    "window_start": _iso(ws),
                    "window_end": _iso(ws + timedelta(seconds=WINDOW_S)),
                    "counts_by_class": by_class,
                    "volume": sum(by_class.values()),
                    "lane": 0,
                }
            )
            if by_class:
                key = (cam, ws.hour)
                bucket = flow.by_camera_hour.setdefault(key, {})
                bucket[arm] = bucket.get(arm, 0) + sum(by_class.values())
    return flow


def busiest_junction(
    flow: CityFlow, *, min_arms: int = 2, hour: int | None = None
) -> tuple[str, int, dict[str, int]]:
    """The (camera, hour) with the most arriving traffic among those seeing >= ``min_arms`` arms.

    Returns ``(camera_id, hour, {arm: vehicles in that hour})``.
    """
    best: tuple[str, int, dict[str, int]] | None = None
    for (cam, h), arms in flow.by_camera_hour.items():
        if hour is not None and h != hour:
            continue
        if len(arms) < min_arms:
            continue
        if best is None or sum(arms.values()) > sum(best[2].values()):
            best = (cam, h, dict(arms))
    if best is None:
        raise ValueError(f"no camera saw traffic from at least {min_arms} arms")
    return best


def junction_records(flow: CityFlow, camera_id: str, hour: int) -> list[dict[str, Any]]:
    """The twelve 5-minute windows (zeros included) for one camera in one hour."""
    ws0 = flow.start + timedelta(hours=hour)
    keep = {_iso(ws0 + timedelta(seconds=i * WINDOW_S)) for i in range(3600 // WINDOW_S)}
    return [r for r in flow.records if r["camera_id"] == camera_id and r["window_start"] in keep]
