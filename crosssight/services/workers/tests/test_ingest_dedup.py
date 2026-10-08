"""Unit tests for ingest deduplication logic."""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from workers.dedup import DEDUP_WINDOW_SEC, DedupKey, HybridDedup, InMemoryDedup
from workers.ingest import IngestWorker


class FakeRedisDedup:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def is_duplicate(self, plate_norm: str, camera_id: str) -> bool:
        key = f"{plate_norm}:{camera_id}"
        if key in self.keys:
            return True
        self.keys.add(key)
        return False


@pytest.mark.asyncio
async def test_in_memory_dedup_within_window():
    dedup = InMemoryDedup()
    t0 = 1_000_000.0
    assert dedup.is_duplicate("BR01AB1234", "cam-1", t0) is False
    assert dedup.is_duplicate("BR01AB1234", "cam-1", t0 + 5) is True
    assert dedup.is_duplicate("BR01AB1234", "cam-2", t0 + 5) is False


@pytest.mark.asyncio
async def test_in_memory_dedup_expires_after_window():
    dedup = InMemoryDedup()
    t0 = time.time()
    assert dedup.is_duplicate("DL3CAB1234", "cam-1", t0) is False
    assert dedup.is_duplicate("DL3CAB1234", "cam-1", t0 + DEDUP_WINDOW_SEC + 1) is False


@pytest.mark.asyncio
async def test_in_memory_dedup_out_of_order_timestamps():
    """Stale keys inserted after a newer one must still expire, and in-window hits stay duplicates."""
    dedup = InMemoryDedup()
    newer = 2_000.0
    older = 1_000.0
    assert dedup.is_duplicate("MH01NEW001", "cam-1", newer) is False
    assert dedup.is_duplicate("MH01OLD001", "cam-1", older) is False
    # In-window relative to the older event's own timestamp.
    assert dedup.is_duplicate("MH01OLD001", "cam-1", older + 5) is True
    # A later event whose window does not cover the older key must expire it,
    # even though that key is not at the front of the insertion-ordered dict.
    assert dedup.is_duplicate("MH01NEW001", "cam-1", newer + 1) is True
    assert DedupKey(plate_norm="MH01OLD001", camera_id="cam-1") not in dedup._seen
    assert dedup.is_duplicate("MH01OLD001", "cam-1", newer + 1) is False


@pytest.mark.asyncio
async def test_hybrid_dedup_uses_memory_first():
    memory = InMemoryDedup()
    redis = FakeRedisDedup()
    hybrid = HybridDedup(memory, redis)  # type: ignore[arg-type]
    t0 = 1_000_000.0
    assert await hybrid.is_duplicate("MH12AB1234", "cam-3", t0) is False
    assert await hybrid.is_duplicate("MH12AB1234", "cam-3", t0 + 2) is True


@pytest.mark.asyncio
async def test_commit_waits_until_flush_succeeds():
    """A failed ClickHouse insert must not commit Kafka offsets."""
    worker = IngestWorker.__new__(IngestWorker)
    worker._buffer = [{"event_id": "evt-1"}]
    worker._heatmap_buffer = {(1, datetime(2026, 1, 1, tzinfo=UTC)): 1}
    worker._pending_offsets = {("anpr.reads.v1", 0): 11}
    worker._ch = AsyncMock()
    worker._ch.insert_reads_async = AsyncMock(side_effect=RuntimeError("clickhouse down"))
    worker._ch.insert_heatmap_1min_async = AsyncMock()
    worker._consumer = AsyncMock()

    with pytest.raises(RuntimeError, match="clickhouse down"):
        await worker._flush_buffer()

    worker._consumer.commit.assert_not_awaited()
    assert worker._pending_offsets == {("anpr.reads.v1", 0): 11}
    assert worker._buffer

    order: list[str] = []

    async def _insert_reads(_batch):
        order.append("insert_reads")

    async def _insert_heatmap(_rows):
        order.append("insert_heatmap")

    async def _commit(_offsets=None):
        order.append("commit")

    worker._ch.insert_reads_async = AsyncMock(side_effect=_insert_reads)
    worker._ch.insert_heatmap_1min_async = AsyncMock(side_effect=_insert_heatmap)
    worker._consumer.commit = AsyncMock(side_effect=_commit)

    await worker._flush_buffer()
    assert order == ["insert_reads", "insert_heatmap", "commit"]
    worker._consumer.commit.assert_awaited()


@pytest.mark.asyncio
async def test_hybrid_dedup_without_redis():
    memory = InMemoryDedup()
    hybrid = HybridDedup(memory, None)
    t0 = 1_000_000.0
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0) is False
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0 + 1) is True
