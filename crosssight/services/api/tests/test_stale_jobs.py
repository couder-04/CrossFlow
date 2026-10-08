"""Stuck processing jobs become failed on startup and can be queued again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from api.jobs import apply_stale_job_recovery, requeue


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, uploads: list, exports: list) -> None:
        self.uploads = uploads
        self.exports = exports
        self.commits = 0

    async def execute(self, statement):
        text = str(statement)
        if "uploads" in text:
            return _Rows(self.uploads)
        return _Rows(self.exports)

    async def commit(self) -> None:
        self.commits += 1


def test_stale_processing_row_fails_and_retry_requeues():
    import asyncio

    stuck = SimpleNamespace(
        id=uuid4(),
        status="processing",
        error=None,
        updated_at=datetime.now(UTC) - timedelta(minutes=45),
        created_at=datetime.now(UTC) - timedelta(minutes=45),
    )
    session = _Session([stuck], [])
    changed = asyncio.run(apply_stale_job_recovery(session, stale_minutes=30))
    assert changed == 1
    assert stuck.status == "failed"
    assert stuck.error == "worker_lost"
    requeue(stuck)
    assert stuck.status == "queued"
    assert stuck.error is None
