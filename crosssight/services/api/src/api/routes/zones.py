"""Zone routes."""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, status
from geoalchemy2 import WKTElement
from geoalchemy2.functions import ST_AsGeoJSON
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from api.db import Zone
from api.deps import AdminUserDep, SessionDep, UserDep
from api.schemas import ZoneCreate, ZoneOut, ZoneUpdate

router = APIRouter(prefix="/zones", tags=["zones"])


def _zone_out(row: Zone, geojson_str: str) -> ZoneOut:
    return ZoneOut(
        id=row.id,
        name=row.name,
        kind=row.kind,  # type: ignore[arg-type]
        active_hours=row.active_hours,
        geojson=json.loads(geojson_str),
    )


@router.get("", response_model=list[ZoneOut])
async def list_zones(session: SessionDep, _user: UserDep) -> list[ZoneOut]:
    result = await session.execute(select(Zone, ST_AsGeoJSON(Zone.geom).label("gj")))
    return [_zone_out(z, gj) for z, gj in result.all()]


@router.get("/{zone_id}", response_model=ZoneOut)
async def get_zone(zone_id: str, session: SessionDep, _user: UserDep) -> ZoneOut:
    result = await session.execute(
        select(Zone, ST_AsGeoJSON(Zone.geom).label("gj")).where(Zone.id == zone_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    zone, gj = row
    return _zone_out(zone, gj)


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
async def create_zone(body: ZoneCreate, session: SessionDep, _admin: AdminUserDep) -> ZoneOut:
    if await session.get(Zone, body.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Zone already exists")
    geom = _geojson_to_wkt(body.geojson)
    zone = Zone(
        id=body.id,
        name=body.name,
        kind=body.kind,
        geom=geom,
        active_hours=body.active_hours,
    )
    session.add(zone)
    await session.commit()
    return _zone_out(zone, json.dumps(body.geojson))


@router.put("/{zone_id}", response_model=ZoneOut)
async def update_zone(
    zone_id: str,
    body: ZoneUpdate,
    session: SessionDep,
    _admin: AdminUserDep,
) -> ZoneOut:
    zone = await session.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    if body.name is not None:
        zone.name = body.name
    if body.kind is not None:
        zone.kind = body.kind
    if body.active_hours is not None:
        zone.active_hours = body.active_hours
    if body.geojson is not None:
        zone.geom = _geojson_to_wkt(body.geojson)
    await session.commit()
    result = await session.execute(select(ST_AsGeoJSON(Zone.geom)).where(Zone.id == zone_id))
    gj = result.scalar_one()
    return _zone_out(zone, gj)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(zone_id: str, session: SessionDep, _admin: AdminUserDep) -> None:
    result = await session.execute(delete(Zone).where(Zone.id == zone_id))
    if cast(CursorResult[Any], result).rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    await session.commit()


def _geojson_to_wkt(geojson: dict) -> WKTElement:
    geom_type = geojson.get("type")
    coords = geojson.get("coordinates")
    if geom_type == "Polygon" and coords:
        rings = []
        for ring in coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
            rings.append(f"({pts})")
        wkt = f"POLYGON({', '.join(rings)})"
        return WKTElement(wkt, srid=4326)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported GeoJSON geometry"
    )
