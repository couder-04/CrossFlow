"""Alert management routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from anpr_common.config import Settings
from anpr_common.intelligence.access import role_can
from anpr_common.intelligence.review import ReviewError, transition_status
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.auditutil import audit_row
from api.db import AlertReviewRow, AlertRow
from api.deps import MinioPresignDep, OperatorUserDep, SessionDep, SettingsDep, UserDep
from api.schemas import AlertActionBody, AlertOut, DispatchBody, ReviewBody
from api.video_feeds import camera_in_source, normalize_source

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _alert_out(row: AlertRow, crop_url: str | None = None) -> AlertOut:
    return AlertOut(
        id=row.id,
        type=row.type,
        severity=row.severity,
        plate_norm=row.plate_norm,
        camera_ids=list(row.camera_ids or []),
        evidence=row.evidence or {},
        status=row.status,
        needs_verification=row.needs_verification,
        ack_by=row.ack_by,
        dispatched_to=row.dispatched_to,
        closed_note=row.closed_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        crop_url=crop_url,
    )


def _crop_url(
    minio,
    settings: Settings,
    evidence: dict[str, Any],
) -> str | None:
    crop_key = evidence.get("crop_key")
    if not crop_key:
        reads = evidence.get("reads") or []
        for r in reads:
            if isinstance(r, dict) and r.get("crop_key"):
                crop_key = r["crop_key"]
                break
    if not crop_key:
        return None
    try:
        return minio.presigned_get_object(
            settings.minio_bucket,
            crop_key,
            expires=timedelta(hours=1),
        )
    except Exception:  # noqa: BLE001
        return None


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    session: SessionDep,
    _user: UserDep,
    status_filter: str | None = Query(None, alias="status"),
    type_filter: str | None = Query(None, alias="type"),
    severity: str | None = Query(None),
    source: str = Query("sim"),
) -> list[AlertOut]:
    stmt = select(AlertRow).order_by(AlertRow.created_at.desc())
    if status_filter:
        stmt = stmt.where(AlertRow.status == status_filter)
    if type_filter:
        stmt = stmt.where(AlertRow.type == type_filter)
    if severity:
        stmt = stmt.where(AlertRow.severity == severity)
    result = await session.execute(stmt)
    mode = normalize_source(source)
    rows = [row for row in result.scalars() if _alert_in_source(list(row.camera_ids or []), mode)]
    return [_alert_out(r) for r in rows]


def _alert_in_source(camera_ids: list[str], source: str) -> bool:
    video_hit = any(camera_in_source(camera_id, "video") for camera_id in camera_ids)
    if source == "video":
        return video_hit
    return not video_hit


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(
    alert_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioPresignDep,
    _user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    url = _crop_url(minio, settings, row.evidence or {})
    return _alert_out(row, crop_url=url)


@router.post("/{alert_id}/ack", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: UUID,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "acknowledged"
    row.ack_by = user.username
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return _alert_out(row)


@router.post("/{alert_id}/dispatch", response_model=AlertOut)
async def dispatch_alert(
    alert_id: UUID,
    body: DispatchBody,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "dispatched"
    row.dispatched_to = body.dispatched_to
    row.ack_by = row.ack_by or user.username
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return _alert_out(row)


@router.post("/{alert_id}/close", response_model=AlertOut)
async def close_alert(
    alert_id: UUID,
    body: AlertActionBody,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    if not body.note or not body.note.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Close requires a note")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "closed"
    row.closed_note = body.note.strip()
    row.updated_at = datetime.now(UTC)
    session.add(
        audit_row(user, "alert_close", plate_norm=row.plate_norm, params={"alert_id": str(row.id)})
    )
    await session.commit()
    return _alert_out(row)


@router.get("/{alert_id}/reviews")
async def list_reviews(alert_id: UUID, session: SessionDep, _user: UserDep) -> list[dict]:
    result = await session.execute(
        select(AlertReviewRow)
        .where(AlertReviewRow.alert_id == alert_id)
        .order_by(AlertReviewRow.created_at)
    )
    return [
        {
            "id": str(row.id),
            "actor": row.actor,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "note": row.note,
            "created_at": row.created_at.isoformat(),
        }
        for row in result.scalars()
    ]


@router.post("/{alert_id}/review", response_model=AlertOut)
async def review_alert(
    alert_id: UUID,
    body: ReviewBody,
    session: SessionDep,
    user: OperatorUserDep,
) -> AlertOut:
    if not role_can(user.role.value, "review_alert"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted to review")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    try:
        target = transition_status(row.status, body.status, body.note)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    previous = row.status
    row.status = target
    if body.note and body.note.strip():
        row.closed_note = body.note.strip()
    row.ack_by = row.ack_by or user.username
    row.updated_at = datetime.now(UTC)
    session.add(
        AlertReviewRow(
            alert_id=row.id,
            actor=user.username,
            from_status=previous,
            to_status=target,
            note=body.note.strip() if body.note else None,
        )
    )
    session.add(
        audit_row(
            user,
            "alert_review",
            plate_norm=row.plate_norm,
            params={"alert_id": str(row.id), "from": previous, "to": target},
        )
    )
    await session.commit()
    return _alert_out(row)
