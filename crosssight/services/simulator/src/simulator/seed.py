"""PostGIS / Redis seeding for cameras, zones, users, and watchlist."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import h3
import networkx as nx
import numpy as np
import psycopg2
import psycopg2.extras
import redis
from anpr_common.geo import latlng_to_h3
from anpr_common.passwords import hash_password
from shapely.geometry import MultiPoint, Polygon

from simulator.cameras import Camera, compute_camera_pairs, place_cameras
from simulator.graph import load_graph
from simulator.scenarios import (
    build_scenarios,
    tag_vehicles_for_scenarios,
    write_scenarios,
)
from simulator.vehicles import generate_fleet

if TYPE_CHECKING:
    from anpr_common.config import Settings

logger = logging.getLogger(__name__)


def connect_pg(settings: Settings):
    return psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
    )


def connect_redis(settings: Settings) -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


def _h3_polygon(cell: str) -> str:
    boundary = h3.cell_to_boundary(cell)
    # PostGIS expects lon lat pairs
    coords = ", ".join(f"{lon} {lat}" for lat, lon in boundary)
    first = boundary[0]
    if boundary[-1] != first:
        coords += f", {first[1]} {first[0]}"
    return f"POLYGON(({coords}))"


def build_zones(
    cameras: list[Camera],
    graph: nx.MultiDiGraph,
    settings: Settings,
    seed: int = 11,
) -> list[dict[str, Any]]:
    cells: set[str] = set()
    for cam in cameras:
        cells.add(h3.int_to_str(latlng_to_h3(cam.lat, cam.lng, settings.h3_od_res)))

    zones: list[dict[str, Any]] = []
    for idx, cell in enumerate(sorted(cells)):
        zones.append(
            {
                "id": f"ward-{idx + 1:03d}",
                "name": f"Ward {idx + 1}",
                "kind": "ward",
                "h3_cell": cell,
                "wkt": _h3_polygon(cell),
                "active_hours": None,
                "centroid": h3.cell_to_latlng(cell),
            }
        )

    ranked = sorted(cameras, key=lambda c: c.betweenness, reverse=True)
    for idx in range(2):
        cluster = ranked[idx * 5 : idx * 5 + 8] or ranked[:5]
        pts = MultiPoint([(c.lng, c.lat) for c in cluster])
        hull = pts.convex_hull.buffer(0.004)
        if isinstance(hull, Polygon):
            coords = list(hull.exterior.coords)
            wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in coords) + "))"
            lat = float(np.mean([c.lat for c in cluster]))
            lng = float(np.mean([c.lng for c in cluster]))
            zones.append(
                {
                    "id": f"sensitive-{idx + 1}",
                    "name": f"Sensitive Zone {idx + 1}",
                    "kind": "sensitive",
                    "wkt": wkt,
                    "active_hours": None,
                    "centroid": (lat, lng),
                }
            )

    tail = ranked[-10:] or ranked
    pts = MultiPoint([(c.lng, c.lat) for c in tail])
    hull = pts.convex_hull.buffer(0.005)
    coords = list(hull.exterior.coords)
    wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in coords) + "))"
    zones.append(
        {
            "id": "restricted-1",
            "name": "Restricted Night Zone",
            "kind": "restricted",
            "wkt": wkt,
            "active_hours": {"start": "22:00", "end": "06:00"},
            "centroid": (
                float(np.mean([c.lat for c in tail])),
                float(np.mean([c.lng for c in tail])),
            ),
        }
    )
    return zones


def _clear_tables(cur) -> None:
    cur.execute("DELETE FROM audit_log")
    cur.execute("DELETE FROM alerts")
    cur.execute("DELETE FROM camera_pairs")
    cur.execute("DELETE FROM watchlist")
    cur.execute("DELETE FROM zones")
    cur.execute("DELETE FROM cameras")
    cur.execute("DELETE FROM users")


def seed_cameras(cur, cameras: list[Camera]) -> None:
    rows = [
        (
            c.id,
            c.name,
            c.lng,
            c.lat,
            c.heading_deg,
            c.lanes,
            c.allowed_direction,
            c.osm_u,
            c.osm_v,
            "active",
        )
        for c in cameras
    ]
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO cameras (id, name, geom, heading_deg, lanes, allowed_direction, osm_u, osm_v, status)
        VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            geom = EXCLUDED.geom,
            heading_deg = EXCLUDED.heading_deg,
            lanes = EXCLUDED.lanes,
            allowed_direction = EXCLUDED.allowed_direction,
            osm_u = EXCLUDED.osm_u,
            osm_v = EXCLUDED.osm_v,
            status = EXCLUDED.status
        """,
        rows,
    )


def seed_zones(cur, zones: list[dict[str, Any]]) -> None:
    for zone in zones:
        cur.execute(
            """
            INSERT INTO zones (id, name, kind, geom, active_hours)
            VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326), %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                kind = EXCLUDED.kind,
                geom = EXCLUDED.geom,
                active_hours = EXCLUDED.active_hours
            """,
            (
                zone["id"],
                zone["name"],
                zone["kind"],
                zone["wkt"],
                json.dumps(zone["active_hours"]) if zone.get("active_hours") else None,
            ),
        )


def seed_camera_pairs(cur, pairs: list[dict[str, object]]) -> None:
    rows = [(p["camera_a"], p["camera_b"], p["distance_m"], p["adjacent"]) for p in pairs]
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO camera_pairs (camera_a, camera_b, distance_m, adjacent)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (camera_a, camera_b) DO UPDATE SET
            distance_m = EXCLUDED.distance_m,
            adjacent = EXCLUDED.adjacent
        """,
        rows,
    )


def seed_users(cur, settings: Settings) -> None:
    users = [
        (settings.seed_admin_user, settings.seed_admin_pass, "admin"),
        (settings.seed_operator_user, settings.seed_operator_pass, "operator"),
        (settings.seed_analyst_user, settings.seed_analyst_pass, "analyst"),
    ]
    for username, password, role in users:
        pw_hash = hash_password(password)
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            """,
            (username, pw_hash, role),
        )


def seed_watchlist(cur, plates: list[str], redis_client: redis.Redis | None = None) -> None:
    for plate in plates:
        cur.execute(
            """
            INSERT INTO watchlist (plate_norm, reason, severity, added_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (plate_norm) DO UPDATE SET reason = EXCLUDED.reason, severity = EXCLUDED.severity
            """,
            (plate, "Scenario watchlist seed", "high", "simulator"),
        )
    if redis_client is not None and plates:
        redis_client.delete("watchlist:set")
        redis_client.sadd("watchlist:set", *plates)


def run_seed(
    settings: Settings,
    force_synthetic: bool = False,
) -> dict[str, Any]:
    graph = load_graph(settings, force_synthetic=force_synthetic)
    cameras = place_cameras(graph, settings)
    zones = build_zones(cameras, graph, settings)
    pairs = compute_camera_pairs(graph, cameras)
    bundle = build_scenarios(cameras, graph, zones, settings)
    write_scenarios(bundle)

    zone_ids = [z["id"] for z in zones]
    vehicles = generate_fleet(settings, zone_ids, reserved_plates=bundle.all_scenario_plates)
    tag_vehicles_for_scenarios(vehicles, bundle)

    conn = connect_pg(settings)
    redis_client: redis.Redis | None = None
    try:
        redis_client = connect_redis(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unavailable during seed (%s)", exc)

    try:
        with conn, conn.cursor() as cur:
            _clear_tables(cur)
            seed_cameras(cur, cameras)
            seed_zones(cur, zones)
            seed_camera_pairs(cur, pairs)
            seed_users(cur, settings)
            seed_watchlist(cur, bundle.watchlist_plates, redis_client)
        logger.info(
            "Seeded %d cameras, %d zones, %d pairs, %d watchlist plates",
            len(cameras),
            len(zones),
            len(pairs),
            len(bundle.watchlist_plates),
        )
    finally:
        conn.close()

    return {
        "graph": graph,
        "cameras": cameras,
        "zones": zones,
        "pairs": pairs,
        "scenarios": bundle,
        "vehicles": vehicles,
    }
