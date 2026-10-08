"""Media uploads and retry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from anpr_common.intelligence.filesafety import object_key, validate_upload
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from api._transfer_helpers import (
    _MEDIA_ACTION,
    _dt,
    _forbid,
    _process_media,
    _store_file,
    _stream_upload,
    _upload_out,
)
from api.auditutil import audit_row
from api.db import Camera, UploadRow
from api.deps import MinioDep, SessionDep, SettingsDep, UserDep
from api.jobs import FAILED, requeue

router = APIRouter(tags=["transfers"])


@router.post("/uploads/media")
async def upload_media(
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    kind: str = Form(...),
    camera_id: str = Form(...),
    file: UploadFile = File(...),
    captured_at: str | None = Form(None),
    zone_id: str | None = Form(None),
) -> dict[str, Any]:
    action = _MEDIA_ACTION.get(kind)
    if action is None:
        raise HTTPException(status_code=400, detail="kind must be video or image")
    _forbid(user.role.value, action)
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=400, detail="Unknown camera")
    kind_limit = (
        settings.max_upload_video_bytes if kind == "video" else settings.max_upload_image_bytes
    )
    limit = min(kind_limit, settings.max_upload_bytes)
    path, total, digest, header = await _stream_upload(file, limit)
    try:
        try:
            meta = validate_upload(
                filename=file.filename or f"upload.{'mp4' if kind == 'video' else 'jpg'}",
                data=header if header else b"",
                kind=kind,
                max_bytes=limit,
                claimed_type=file.content_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        meta["size"] = total
        row = UploadRow(
            kind=kind,
            filename=meta["filename"],
            mime=meta["mime"],
            size_bytes=meta["size"],
            status="queued",
            uploaded_by=user.username,
            camera_id=camera_id,
            zone_id=zone_id,
            captured_at=_dt(captured_at) if captured_at else datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        key = object_key(f"uploads/{kind}", str(row.id), meta["filename"])
        _store_file(minio, settings, key, path, total, meta["mime"])
        row.object_key = key
        session.add(
            audit_row(
                user,
                "media_upload",
                params={"id": str(row.id), "kind": kind, "camera_id": camera_id, "sha256": digest},
            )
        )
        await session.commit()
        background.add_task(_process_media, row.id)
        await session.refresh(row)
        return _upload_out(row)
    finally:
        path.unlink(missing_ok=True)


@router.post("/uploads/{upload_id}/retry")
async def retry_upload(
    upload_id: UUID,
    background: BackgroundTasks,
    session: SessionDep,
    _user: UserDep,
) -> dict[str, Any]:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if row.status != FAILED:
        raise HTTPException(status_code=409, detail="Only a failed upload can be retried")
    requeue(row)
    await session.commit()
    background.add_task(_process_media, row.id)
    await session.refresh(row)
    return _upload_out(row)
