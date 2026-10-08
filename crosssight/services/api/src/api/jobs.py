"""Recover background export and upload jobs that died with the API process."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from api.db import ExportRow, UploadRow

# Stored status values are lowercase; the writers in the upload and export routes use these.
PROCESSING = "processing"
FAILED = "failed"
QUEUED = "queued"
WORKER_LOST = "worker_lost"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def apply_stale_job_recovery(
    session, *, now: datetime | None = None, stale_minutes: int = 30
) -> int:
    """Mark processing rows older than ``stale_minutes`` as failed and retryable."""
    moment = _aware(now) or datetime.now(UTC)
    cutoff = moment - timedelta(minutes=stale_minutes)
    changed = 0
    for model, stamp in ((UploadRow, "updated_at"), (ExportRow, "created_at")):
        result = await session.execute(select(model).where(model.status == PROCESSING))
        for row in result.scalars().all():
            seen = _aware(getattr(row, stamp, None)) or _aware(getattr(row, "created_at", None))
            if seen is None or seen >= cutoff:
                continue
            row.status = FAILED
            row.error = WORKER_LOST
            changed += 1
    await session.commit()
    return changed


def requeue(row) -> None:
    """Move a failed job back to queued. Caller commits."""
    row.status = QUEUED
    row.error = None
