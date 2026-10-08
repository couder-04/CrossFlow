"""Camera routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, status
from geoalchemy2 import WKTElement
from geoalchemy2.functions import ST_X, ST_Y
from minio.error import S3Error
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from api.db import Camera
from api.deps import AdminUserDep, MinioDep, MinioPresignDep, SessionDep, SettingsDep, UserDep
from api.schemas import CameraCreate, CameraOut, CameraUpdate
from api.video_feeds import camera_in_source, normalize_source

router = APIRouter(prefix="/cameras", tags=["cameras"])


def frame_object_key(camera_id: str) -> str:
    return f"frames/latest/{camera_id}.jpg"


def frame_exists(minio, settings, camera_id: str) -> bool:
    try:
        minio.stat_object(settings.minio_bucket, frame_object_key(camera_id))
        return True
    except (S3Error, OSError):
        return False


def _camera_out(row: Camera, lat: float, lng: float, *, has_live_frame: bool = False) -> CameraOut:
    return CameraOut(
        id=row.id,
        name=row.name,
        lat=lat,
        lng=lng,
        heading_deg=row.heading_deg,
        lanes=row.lanes,
        allowed_direction=row.allowed_direction,
        osm_u=row.osm_u,
        osm_v=row.osm_v,
        status=row.status,
        has_live_frame=has_live_frame,
    )


@router.get("", response_model=list[CameraOut])
async def list_cameras(
    session: SessionDep,
    _user: UserDep,
    minio: MinioDep,
    settings: SettingsDep,
    source: str = Query("sim"),
) -> list[CameraOut]:
    result = await session.execute(
        select(Camera, ST_Y(Camera.geom).label("lat"), ST_X(Camera.geom).label("lng"))
    )
    mode = normalize_source(source)
    rows = [row for row in result.all() if camera_in_source(row[0].id, mode)]
    return [
        _camera_out(
            cam,
            float(lat),
            float(lng),
            has_live_frame=frame_exists(minio, settings, cam.id),
        )
        for cam, lat, lng in rows
    ]


@router.get("/{camera_id}/frame")
async def camera_frame(
    camera_id: str,
    _user: UserDep,
    minio: MinioDep,
    presign: MinioPresignDep,
    settings: SettingsDep,
) -> dict[str, str]:
    """Short-TTL presigned URL for the latest annotated JPEG. Bytes stay in MinIO."""
    key = frame_object_key(camera_id)
    if not frame_exists(minio, settings, camera_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live frame not found")
    url = presign.presigned_get_object(
        settings.minio_bucket,
        key,
        expires=timedelta(minutes=5),
    )
    return {"url": url, "key": key}


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(
    camera_id: str,
    session: SessionDep,
    _user: UserDep,
    minio: MinioDep,
    settings: SettingsDep,
) -> CameraOut:
    result = await session.execute(
        select(Camera, ST_Y(Camera.geom).label("lat"), ST_X(Camera.geom).label("lng")).where(
            Camera.id == camera_id
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    cam, lat, lng = row
    return _camera_out(
        cam,
        float(lat),
        float(lng),
        has_live_frame=frame_exists(minio, settings, cam.id),
    )


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(
    body: CameraCreate,
    session: SessionDep,
    _admin: AdminUserDep,
) -> CameraOut:
    existing = await session.get(Camera, body.id)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera already exists")
    cam = Camera(
        id=body.id,
        name=body.name,
        geom=WKTElement(f"POINT({body.lng} {body.lat})", srid=4326),
        heading_deg=body.heading_deg,
        lanes=body.lanes,
        allowed_direction=body.allowed_direction,
        osm_u=body.osm_u,
        osm_v=body.osm_v,
        status=body.status,
    )
    session.add(cam)
    await session.commit()
    return _camera_out(cam, body.lat, body.lng)


@router.put("/{camera_id}", response_model=CameraOut)
async def update_camera(
    camera_id: str,
    body: CameraUpdate,
    session: SessionDep,
    _admin: AdminUserDep,
) -> CameraOut:
    cam = await session.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    if body.name is not None:
        cam.name = body.name
    if body.heading_deg is not None:
        cam.heading_deg = body.heading_deg
    if body.lanes is not None:
        cam.lanes = body.lanes
    if body.allowed_direction is not None:
        cam.allowed_direction = body.allowed_direction
    if body.osm_u is not None:
        cam.osm_u = body.osm_u
    if body.osm_v is not None:
        cam.osm_v = body.osm_v
    if body.status is not None:
        cam.status = body.status
    lat, lng = 0.0, 0.0
    if body.lat is not None and body.lng is not None:
        cam.geom = WKTElement(f"POINT({body.lng} {body.lat})", srid=4326)
        lat, lng = body.lat, body.lng
    else:
        result = await session.execute(
            select(ST_Y(Camera.geom), ST_X(Camera.geom)).where(Camera.id == camera_id)
        )
        lat, lng = result.one()
        lat, lng = float(lat), float(lng)
    await session.commit()
    return _camera_out(cam, lat, lng)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: str,
    session: SessionDep,
    _admin: AdminUserDep,
) -> None:
    result = await session.execute(delete(Camera).where(Camera.id == camera_id))
    if cast(CursorResult[Any], result).rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    await session.commit()
