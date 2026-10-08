"""Export jobs, download, and retry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from anpr_common.intelligence.access import PLATE_EXPORTS, export_action, role_can
from anpr_common.intelligence.exporters import export_filename
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from api._transfer_helpers import (
    _EXPORT_KINDS,
    _export_out,
    _forbid,
    _run_export,
    read_stored,
)
from api.auditutil import audit_row
from api.db import ExportRow
from api.deps import MinioDep, SessionDep, SettingsDep, UserDep
from api.jobs import FAILED, requeue

router = APIRouter(tags=["transfers"])


@router.post("/exports")
async def create_export(
    background: BackgroundTasks,
    session: SessionDep,
    user: UserDep,
    kind: str = Form(...),
    fmt: str = Form(...),
    filters: str = Form("{}"),
) -> dict[str, Any]:
    if kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=400, detail="Unknown export kind")
    if fmt not in {"csv", "pdf", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv, pdf, or json")
    _forbid(user.role.value, export_action(kind))
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="filters must be JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")
    now = datetime.now(UTC)
    row = ExportRow(
        kind=kind,
        format=fmt,
        status="queued",
        requested_by=user.username,
        filters=parsed,
        expires_at=now + timedelta(days=7),
    )
    session.add(row)
    await session.flush()
    row.filename = export_filename(kind, fmt, now, str(row.id))
    session.add(
        audit_row(
            user,
            "export_create",
            params={"id": str(row.id), "kind": kind, "format": fmt, "filters": parsed},
        )
    )
    await session.commit()
    background.add_task(_run_export, row.id, user.role.value)
    await session.refresh(row)
    return _export_out(row)


@router.post("/exports/{export_id}/retry")
async def retry_export(
    export_id: UUID,
    background: BackgroundTasks,
    session: SessionDep,
    user: UserDep,
) -> dict[str, Any]:
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if row.status != FAILED:
        raise HTTPException(status_code=409, detail="Only a failed export can be retried")
    _forbid(user.role.value, export_action(row.kind))
    requeue(row)
    await session.commit()
    background.add_task(_run_export, row.id, user.role.value)
    await session.refresh(row)
    return _export_out(row)


@router.get("/exports")
async def list_exports(
    session: SessionDep, user: UserDep, limit: int = Query(50, ge=1, le=200)
) -> list[dict]:
    stmt = select(ExportRow)
    if not role_can(user.role.value, "export_plates"):
        # Plate-level exports are gated by export_plates everywhere else; don't list them either.
        stmt = stmt.where(ExportRow.kind.not_in(sorted(PLATE_EXPORTS)))
    result = await session.execute(stmt.order_by(ExportRow.created_at.desc()).limit(limit))
    return [_export_out(row) for row in result.scalars()]


@router.get("/exports/{export_id}")
async def get_export(export_id: UUID, session: SessionDep, user: UserDep) -> dict:
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    _forbid(user.role.value, export_action(row.kind))
    return _export_out(row)


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
):
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    _forbid(user.role.value, export_action(row.kind))
    if row.expires_at and row.expires_at < datetime.now(UTC):
        row.status = "expired"
        await session.commit()
        raise HTTPException(status_code=410, detail="Export expired")
    if row.status != "ready" or not row.object_key:
        raise HTTPException(status_code=409, detail="Export is not ready")
    session.add(audit_row(user, "export_download", params={"id": str(row.id), "kind": row.kind}))
    await session.commit()
    payload = read_stored(minio, settings, row.object_key)
    media = (
        "text/csv; charset=utf-8"
        if row.format == "csv"
        else "application/pdf"
        if row.format == "pdf"
        else "application/json"
    )
    filename = (row.filename or "export.bin").replace('"', "")
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
