"""Audit log helper used by review, upload, and download routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from api.auth import UserContext
from api.db import AuditLogRow


def audit_row(
    user: UserContext,
    action: str,
    *,
    plate_norm: str | None = None,
    case_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> AuditLogRow:
    user_id = None
    if user.id:
        try:
            user_id = UUID(user.id)
        except ValueError:
            user_id = None
    return AuditLogRow(
        user_id=user_id,
        action=action,
        plate_norm=plate_norm,
        case_id=case_id,
        params=params or {},
    )
