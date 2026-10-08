"""Switch the control room between the simulated city and video cameras."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from geoalchemy2 import WKTElement
from pydantic import BaseModel

from api.db import Camera
from api.deps import SessionDep, UserDep
from api.video_feeds import (
    camera_records,
    discover_videos,
    feeds_dir,
    fleet_path,
    runner_alive,
    start_runner,
    stop_runner,
    write_fleet,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceModeBody(BaseModel):
    mode: Literal["sim", "video"]


async def _upsert_video_cameras(session: SessionDep, records: list[dict[str, object]]) -> None:
    for rec in records:
        camera_id = str(rec["id"])
        geom = WKTElement(f"POINT({rec['lng']} {rec['lat']})", srid=4326)
        cam = await session.get(Camera, camera_id)
        config = {"kind": "video", "path": rec["source"]}
        if cam is None:
            session.add(
                Camera(
                    id=camera_id,
                    name=str(rec["name"]),
                    geom=geom,
                    heading_deg=float(str(rec["heading_deg"])),
                    lanes=int(str(rec["lanes"])),
                    status="active",
                    ops_config=config,
                )
            )
        else:
            cam.name = str(rec["name"])
            cam.geom = geom
            cam.heading_deg = float(str(rec["heading_deg"]))
            cam.lanes = int(str(rec["lanes"]))
            cam.status = "active"
            cam.ops_config = config
    await session.commit()


@router.get("/mode")
async def source_mode(_user: UserDep) -> dict[str, object]:
    videos = discover_videos()
    return {
        "running": runner_alive(),
        "video_count": len(videos),
        "feeds_dir": str(feeds_dir()),
    }


@router.post("/mode")
async def set_source_mode(
    body: SourceModeBody,
    session: SessionDep,
    _user: UserDep,
) -> dict[str, object]:
    if body.mode == "sim":
        stop_runner()
        return {"mode": "sim", "running": False, "cameras": []}

    records = camera_records(discover_videos())
    if not records:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No camera videos found. Add files under data/drive_cameras and try again.",
        )
    try:
        await _upsert_video_cameras(session, records)
    except Exception as exc:
        logger.exception("Could not register video cameras")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not register video cameras",
        ) from exc
    config = write_fleet(records, fleet_path())
    try:
        pid = start_runner(config)
    except Exception as exc:
        logger.exception("Could not start video cameras")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not start video cameras",
        ) from exc
    return {
        "mode": "video",
        "running": True,
        "pid": pid,
        "cameras": [{"id": rec["id"], "name": rec["name"]} for rec in records],
    }
