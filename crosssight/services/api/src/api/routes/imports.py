"""Import preview, confirm, and listing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from anpr_common.intelligence.filesafety import object_key, validate_upload
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select

from api._transfer_helpers import (
    _IMPORT_ACTION,
    _apply_preview,
    _build_preview,
    _counts,
    _forbid,
    _read_limited,
    _store,
    _upload_out,
)
from api.auditutil import audit_row
from api.db import UploadRow
from api.deps import MinioDep, SessionDep, SettingsDep, UserDep

router = APIRouter(tags=["transfers"])


@router.post("/imports/preview")
async def preview_import(
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    action = _IMPORT_ACTION.get(kind)
    if action is None:
        raise HTTPException(status_code=400, detail="Unknown import kind")
    _forbid(user.role.value, action)
    data = await _read_limited(file, settings.max_upload_table_bytes)
    file_kind = "geojson" if kind == "zones" else "json" if kind == "calibration" else "csv"
    if kind == "cameras" and (file.filename or "").lower().endswith(".json"):
        file_kind = "json"
    try:
        meta = validate_upload(
            filename=file.filename or "upload.csv",
            data=data,
            kind=file_kind if kind != "cameras" else ("json" if file_kind == "json" else "csv"),
            max_bytes=settings.max_upload_table_bytes,
            claimed_type=file.content_type,
        )
        text = data.decode("utf-8-sig")
        preview = await _build_preview(session, kind, text, file_kind == "json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = UploadRow(
        kind=kind,
        filename=meta["filename"],
        mime=meta["mime"],
        size_bytes=meta["size"],
        status="preview",
        uploaded_by=user.username,
        preview=preview,
    )
    session.add(row)
    await session.flush()
    key = object_key("imports", str(row.id), meta["filename"])
    _store(minio, settings, key, data, meta["mime"])
    row.object_key = key
    session.add(
        audit_row(
            user, "import_preview", params={"id": str(row.id), "kind": kind, **_counts(preview)}
        )
    )
    await session.commit()
    await session.refresh(row)
    return _upload_out(row) | {"preview": preview}


@router.post("/imports/{upload_id}/confirm")
async def confirm_import(
    upload_id: UUID,
    session: SessionDep,
    user: UserDep,
    acknowledge_invalid: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    _forbid(user.role.value, _IMPORT_ACTION.get(row.kind, ""))
    if row.status != "preview":
        raise HTTPException(status_code=409, detail="Import is not waiting for confirmation")
    preview = row.preview or {}
    if preview.get("invalid_count") and not acknowledge_invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid rows must be acknowledged",
                "invalid": preview.get("invalid"),
            },
        )
    applied = await _apply_preview(session, row.kind, preview, user.username, overwrite)
    row.status = "completed"
    row.result = {
        "applied": applied,
        "acknowledged_invalid": acknowledge_invalid,
        "overwrite": overwrite,
    }
    row.updated_at = datetime.now(UTC)
    session.add(
        audit_row(
            user, "import_confirm", params={"id": str(row.id), "kind": row.kind, "applied": applied}
        )
    )
    await session.commit()
    return _upload_out(row)


@router.get("/imports")
async def list_imports(
    session: SessionDep, _user: UserDep, limit: int = Query(50, ge=1, le=200)
) -> list[dict]:
    result = await session.execute(
        select(UploadRow).order_by(UploadRow.created_at.desc()).limit(limit)
    )
    return [_upload_out(row) for row in result.scalars()]


@router.get("/imports/{upload_id}")
async def get_import(upload_id: UUID, session: SessionDep, _user: UserDep) -> dict:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return _upload_out(row) | {"preview": row.preview or {}}
