"""Audit log routes (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from api.db import AuditLogRow
from api.deps import AdminUserDep, SessionDep
from api.schemas import AuditEntryOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(
    session: SessionDep,
    _admin: AdminUserDep,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[AuditEntryOut]:
    result = await session.execute(
        select(AuditLogRow).order_by(AuditLogRow.ts.desc()).limit(limit).offset(offset)
    )
    return [
        AuditEntryOut(
            id=row.id,
            user_id=str(row.user_id) if row.user_id else None,
            action=row.action,
            plate_norm=row.plate_norm,
            case_id=row.case_id,
            params=row.params or {},
            ts=row.ts,
        )
        for row in result.scalars()
    ]
