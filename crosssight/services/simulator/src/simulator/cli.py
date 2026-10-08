"""CLI entrypoint: seed and simulate."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import psycopg2.extras
from anpr_common.config import get_settings

from simulator.cameras import Camera
from simulator.emit import ReadEmitter, build_injected_read, process_traversal
from simulator.graph import load_graph
from simulator.scenarios import (
    InjectedEvent,
    load_scenarios,
    scenario_injections,
    tag_vehicles_for_scenarios,
)
from simulator.seed import connect_pg, run_seed
from simulator.trips import (
    TraversalEvent,
    build_edge_betweenness,
    build_zone_node_map,
    iter_traversals,
    schedule_trips,
)
from simulator.vehicles import Vehicle, generate_fleet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("simulator.cli")


def _load_cameras_from_db(settings) -> list[Camera]:
    conn = connect_pg(settings)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, ST_Y(geom) AS lat, ST_X(geom) AS lng,
                       heading_deg, lanes, allowed_direction, osm_u, osm_v
                FROM cameras ORDER BY id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    cameras: list[Camera] = []
    for row in rows:
        cameras.append(
            Camera(
                id=row["id"],
                name=row["name"],
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                heading_deg=float(row["heading_deg"]),
                lanes=int(row["lanes"]),
                allowed_direction=row["allowed_direction"] or "N",
                osm_u=int(row["osm_u"] or 0),
                osm_v=int(row["osm_v"] or 0),
                node_id=int(row["osm_u"] or 0),
            )
        )
    return cameras


def _load_zones_from_db(settings) -> list[dict]:
    conn = connect_pg(settings)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, kind, active_hours FROM zones ORDER BY id")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _vehicle_by_plate(vehicles: list[Vehicle]) -> dict[str, Vehicle]:
    return {v.plate_norm: v for v in vehicles}


def run_simulation(
    settings,
    speed: float = 60.0,
    backfill_days: int | None = None,
    force_synthetic: bool = False,
    output_file: Path | None = None,
    use_kafka: bool = True,
) -> int:
    graph = load_graph(settings, force_synthetic=force_synthetic)
    cameras = _load_cameras_from_db(settings)
    if not cameras:
        logger.error("No cameras in database — run `python -m simulator.cli seed` first")
        return 1

    zones = _load_zones_from_db(settings)
    zone_node_map = build_zone_node_map(graph, zones)
    scenarios = load_scenarios()
    zone_ids = [z["id"] for z in zones] or ["ward-001"]

    reserved = scenarios.get("watchlist_plates", []) + scenarios.get("expected_alerts", {}).get(
        "cloned_plate", []
    )
    vehicles = generate_fleet(settings, zone_ids, reserved_plates=reserved)
    if scenarios:
        from simulator.scenarios import ScenarioBundle

        bundle = ScenarioBundle(
            watchlist_plates=scenarios.get("watchlist_plates", []),
            cloned_pairs=scenarios.get("cloned_pairs", []),
            convoy=scenarios.get("convoy", {}),
            loiterer=scenarios.get("loiterer", {}),
            wrong_way=scenarios.get("wrong_way", {}),
            restricted_zone=scenarios.get("restricted_zone", {}),
        )
        tag_vehicles_for_scenarios(vehicles, bundle)

    if backfill_days:
        start = datetime.now(UTC) - timedelta(days=backfill_days)
        duration = timedelta(days=backfill_days)
        live = False
    else:
        start = datetime.now(UTC)
        duration = timedelta(hours=12)
        live = True

    plans = schedule_trips(graph, vehicles, zone_node_map, start, duration, settings)
    edge_bc = build_edge_betweenness(graph)
    rng = random.Random(123)

    events: list[tuple[datetime, str, object]] = []
    for plan in plans:
        for traversal in iter_traversals(graph, cameras, plan, edge_bc):
            events.append((traversal.ts, "traversal", traversal))

    if scenarios:
        from simulator.scenarios import ScenarioBundle

        bundle = ScenarioBundle(
            watchlist_plates=scenarios.get("watchlist_plates", []),
            cloned_pairs=scenarios.get("cloned_pairs", []),
            convoy=scenarios.get("convoy", {}),
            loiterer=scenarios.get("loiterer", {}),
            wrong_way=scenarios.get("wrong_way", {}),
            restricted_zone=scenarios.get("restricted_zone", {}),
        )
        for inj in scenario_injections(bundle, cameras, graph, zone_node_map, start):
            events.append((inj.ts, "injection", inj))

    events.sort(key=lambda item: item[0])
    if not events:
        logger.warning("No simulation events generated")
        return 0

    emitter = ReadEmitter(
        settings,
        output_file=output_file,
        use_kafka=use_kafka,
        use_clickhouse=bool(backfill_days),
    )
    emitter.connect()

    cam_by_id = {c.id: c for c in cameras}
    vehicles_by_plate = _vehicle_by_plate(vehicles)
    sim_start_wall = time.monotonic()
    first_ts = events[0][0]

    try:
        for idx, (ts, kind, payload) in enumerate(events):
            if live and speed > 0:
                elapsed_sim = (ts - first_ts).total_seconds()
                target_wall = sim_start_wall + elapsed_sim / speed
                delay = target_wall - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 1.0))

            if kind == "traversal":
                process_traversal(cast(TraversalEvent, payload), emitter, rng)
            else:
                injected = cast(InjectedEvent, payload)
                cam = cam_by_id[injected.camera_id]
                vehicle = vehicles_by_plate.get(injected.plate_norm)
                read = build_injected_read(injected, cam, vehicle, rng)
                emitter.emit(read, camera_name=cam.name)

            if backfill_days and idx % 5000 == 0 and idx > 0:
                emitter.flush()
                logger.info("Backfill progress: %d / %d events", idx, len(events))
    finally:
        emitter.close()

    logger.info(
        "Simulation complete: emitted=%d skipped=%d events=%d",
        emitter.emitted,
        emitter.skipped,
        len(events),
    )
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    settings = get_settings()
    run_seed(settings, force_synthetic=args.synthetic)
    logger.info("Seed complete")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    settings = get_settings()
    output = Path(args.output) if args.output else None
    return run_simulation(
        settings,
        speed=args.speed,
        backfill_days=args.backfill_days,
        force_synthetic=args.synthetic,
        output_file=output,
        use_kafka=not args.no_kafka,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simulator", description="ANPR road-network simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_p = sub.add_parser("seed", help="Seed graph, cameras, zones, users, watchlist")
    seed_p.add_argument("--synthetic", action="store_true", help="Force synthetic grid graph")
    seed_p.set_defaults(func=cmd_seed)

    sim_p = sub.add_parser("simulate", help="Run live or backfill simulation")
    sim_p.add_argument(
        "--speed", type=float, default=None, help="Live speed multiplier (default from SIM_SPEED)"
    )
    sim_p.add_argument(
        "--backfill-days", type=int, default=None, help="Fast historical backfill in days"
    )
    sim_p.add_argument("--synthetic", action="store_true", help="Force synthetic grid graph")
    sim_p.add_argument("--output", type=str, default=None, help="Optional JSONL output file")
    sim_p.add_argument("--no-kafka", action="store_true", help="Disable Kafka publishing")
    sim_p.set_defaults(func=cmd_simulate)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "simulate" and args.speed is None:
        args.speed = get_settings().sim_speed
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
