"""Alert evidence listing and download."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from anpr_common.intelligence.exporters import overlay_plan
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api._transfer_helpers import (
    _forbid,
)
from api.auditutil import audit_row
from api.db import AlertRow
from api.deps import MinioDep, SessionDep, SettingsDep, UserDep

router = APIRouter(tags=["transfers"])


@router.get("/alerts/{alert_id}/evidence")
async def list_evidence(alert_id: UUID, session: SessionDep, user: UserDep) -> dict:
    _forbid(user.role.value, "download_evidence")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    items = []
    crop = evidence.get("crop_key")
    reads = evidence.get("reads") or []
    if not crop:
        for read in reads:
            if isinstance(read, dict) and read.get("crop_key"):
                crop = read["crop_key"]
                break
    if crop:
        items.append({"kind": "crop", "available": True})
    source = evidence.get("source_video_key")
    for read in reads:
        if isinstance(read, dict) and read.get("source_video_key"):
            source = read["source_video_key"]
    if source:
        items.append({"kind": "video", "available": True})
    return {"alert_id": str(alert_id), "items": items}


@router.get("/alerts/{alert_id}/evidence/{kind}")
async def download_evidence(
    alert_id: UUID,
    kind: str,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
):
    _forbid(user.role.value, "download_evidence")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    key = None
    if kind == "crop":
        key = evidence.get("crop_key")
        for read in evidence.get("reads") or []:
            if not key and isinstance(read, dict):
                key = read.get("crop_key")
        media = "image/jpeg"
        filename = f"crosssight_{alert_id}_crop.jpg"
    elif kind == "video":
        key = evidence.get("source_video_key")
        for read in evidence.get("reads") or []:
            if not key and isinstance(read, dict):
                key = read.get("source_video_key")
        media = "video/mp4"
        filename = f"crosssight_{alert_id}_source.mp4"
    else:
        raise HTTPException(status_code=400, detail="kind must be crop or video")
    if not key:
        raise HTTPException(
            status_code=404, detail="This incident has no stored evidence of that kind"
        )
    session.add(
        audit_row(
            user,
            "evidence_download",
            plate_norm=row.plate_norm,
            params={"alert_id": str(alert_id), "kind": kind},
        )
    )
    await session.commit()
    obj = minio.get_object(settings.minio_bucket, key)
    return StreamingResponse(
        obj.stream(32 * 1024),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/alerts/{alert_id}/evidence/clip")
async def export_clip(
    alert_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    before_s: float = 5,
    after_s: float = 5,
) -> dict[str, Any]:
    _forbid(user.role.value, "download_evidence")
    if before_s < 0 or after_s < 0 or before_s + after_s > 120:
        raise HTTPException(status_code=400, detail="Clip window must be between 0 and 120 seconds")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    source = evidence.get("source_video_key")
    meta = {
        "plate": row.plate_norm,
        "alert": row.type,
        "bbox": evidence.get("bbox"),
        "track_id": evidence.get("track_id"),
    }
    for read in evidence.get("reads") or []:
        if isinstance(read, dict):
            source = source or read.get("source_video_key")
            meta["bbox"] = meta["bbox"] or read.get("bbox")
            meta["track_id"] = (
                meta["track_id"] if meta["track_id"] is not None else read.get("track_id")
            )
    plan = overlay_plan(meta)
    if not source:
        raise HTTPException(status_code=409, detail="Source video is not stored for this incident")
    session.add(
        audit_row(
            user,
            "evidence_clip",
            plate_norm=row.plate_norm,
            params={
                "alert_id": str(alert_id),
                "before_s": before_s,
                "after_s": after_s,
                "overlay": plan,
            },
        )
    )
    await session.commit()
    return {
        "alert_id": str(alert_id),
        "source_available": True,
        "before_s": before_s,
        "after_s": after_s,
        "overlay": plan,
        "download": f"/alerts/{alert_id}/evidence/video",
    }
