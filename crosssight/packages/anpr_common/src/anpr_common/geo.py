"""Geo helpers: H3 cells and bearing utilities."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import h3

from anpr_common.schemas import Direction

if TYPE_CHECKING:
    from anpr_common.config import Settings


def latlng_to_h3(lat: float, lng: float, resolution: int) -> int:
    """Return H3 cell as uint64 integer (h3 v4 API)."""
    cell = h3.latlng_to_cell(lat, lng, resolution)
    return int(h3.str_to_int(cell))


def h3_to_str(cell_int: int) -> str:
    return h3.int_to_str(cell_int)


def enrich_h3(lat: float, lng: float, settings: Settings | None = None) -> tuple[int, int]:
    if settings is None:
        from anpr_common.config import get_settings

        settings = get_settings()
    return (
        latlng_to_h3(lat, lng, settings.h3_heatmap_res),
        latlng_to_h3(lat, lng, settings.h3_od_res),
    )


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 in degrees [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def bearing_to_direction(bearing: float) -> Direction:
    sectors = [
        (22.5, Direction.N),
        (67.5, Direction.NE),
        (112.5, Direction.E),
        (157.5, Direction.SE),
        (202.5, Direction.S),
        (247.5, Direction.SW),
        (292.5, Direction.W),
        (337.5, Direction.NW),
        (360.0, Direction.N),
    ]
    b = bearing % 360.0
    for limit, direction in sectors:
        if b < limit:
            return direction
    return Direction.N


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def opposite_direction(d: Direction | str) -> Direction:
    mapping = {
        Direction.N: Direction.S,
        Direction.NE: Direction.SW,
        Direction.E: Direction.W,
        Direction.SE: Direction.NW,
        Direction.S: Direction.N,
        Direction.SW: Direction.NE,
        Direction.W: Direction.E,
        Direction.NW: Direction.SE,
    }
    if isinstance(d, str):
        d = Direction(d)
    return mapping[d]
