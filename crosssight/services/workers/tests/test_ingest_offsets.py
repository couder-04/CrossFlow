"""Offsets are committed only for reads that were part of the inserted batch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from workers import ingest
from workers.ingest import IngestWorker

TOPIC = "anpr.reads.v1"


class FakeConsumer:
    def __init__(self) -> None:
        self.commits: list[dict[tuple[str, int], int]] = []

    async def commit(self, offsets) -> None:
        self.commits.append({(tp.topic, tp.partition): om.offset for tp, om in offsets.items()})


class GatedClickHouse:
    """insert_reads_async blocks on an Event so the test can interleave consume-loop work."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.batches: list[list[dict]] = []
        self.fail = False

    async def insert_reads_async(self, batch) -> None:
        self.batches.append(list(batch))
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("clickhouse down")

    async def insert_heatmap_1min_async(self, rows) -> None:
        return None


def _worker(ch) -> IngestWorker:
    worker = IngestWorker.__new__(IngestWorker)
    worker._buffer = []
    worker._heatmap_buffer = {}
    worker._pending_offsets = {}
    worker._flush_lock = asyncio.Lock()
    worker._stop = asyncio.Event()
    worker._ch = ch
    worker._consumer = FakeConsumer()
    return worker


def _consume(worker: IngestWorker, partition: int, offset: int) -> None:
    """What _consume_loop does for one accepted read."""
    worker._buffer.append({"event_id": f"{partition}-{offset}"})
    worker._track_offset(TOPIC, partition, offset)


async def test_commit_covers_only_the_snapshotted_reads():
    ch = GatedClickHouse()
    worker = _worker(ch)
    for offset in (10, 11):
        _consume(worker, 0, offset)
    _consume(worker, 1, 5)

    flush = asyncio.create_task(worker._flush_buffer())
    await ch.entered.wait()
    # Reads arriving while the insert is in flight are not part of that batch.
    _consume(worker, 0, 12)
    _consume(worker, 0, 13)
    _consume(worker, 2, 99)
    ch.release.set()
    await flush

    assert len(ch.batches[0]) == 3
    assert worker._consumer.commits == [{(TOPIC, 0): 12, (TOPIC, 1): 6}]  # type: ignore[attr-defined]
    assert worker._pending_offsets == {(TOPIC, 0): 14, (TOPIC, 2): 100}
    assert [r["event_id"] for r in worker._buffer] == ["0-12", "0-13", "2-99"]

    await worker._flush_buffer()  # second flush picks up the rest
    assert worker._consumer.commits[-1] == {(TOPIC, 0): 14, (TOPIC, 2): 100}  # type: ignore[attr-defined]
    assert worker._pending_offsets == {}


async def test_failed_insert_merges_offsets_back_keeping_the_max():
    ch = GatedClickHouse()
    ch.fail = True
    worker = _worker(ch)
    _consume(worker, 0, 10)
    _consume(worker, 1, 3)

    flush = asyncio.create_task(worker._flush_buffer())
    await ch.entered.wait()
    _consume(worker, 0, 20)  # newer than the snapshot, same partition
    ch.release.set()
    with pytest.raises(RuntimeError):
        await flush

    assert worker._consumer.commits == []  # type: ignore[attr-defined]
    assert worker._pending_offsets == {(TOPIC, 0): 21, (TOPIC, 1): 4}
    assert [r["event_id"] for r in worker._buffer] == ["0-10", "1-3", "0-20"]


async def test_flushes_never_overlap():
    ch = GatedClickHouse()
    worker = _worker(ch)
    _consume(worker, 0, 1)

    first = asyncio.create_task(worker._flush_buffer())
    await ch.entered.wait()
    _consume(worker, 0, 2)
    second = asyncio.create_task(worker._flush_buffer())
    await asyncio.sleep(0.05)
    assert len(ch.batches) == 1  # the second flush is waiting for the lock
    ch.release.set()
    await asyncio.gather(first, second)
    assert [len(b) for b in ch.batches] == [1, 1]
    assert worker._consumer.commits == [{(TOPIC, 0): 2}, {(TOPIC, 0): 3}]  # type: ignore[attr-defined]


async def test_batch_loop_survives_flush_errors(monkeypatch):
    monkeypatch.setattr(ingest, "BATCH_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(ingest, "BATCH_MAX_BACKOFF_SEC", 0.02)
    ch = AsyncMock()
    calls = {"n": 0}

    async def insert(batch) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("clickhouse down")

    ch.insert_reads_async = insert
    ch.insert_heatmap_1min_async = AsyncMock()
    worker = _worker(ch)
    _consume(worker, 0, 7)

    task = asyncio.create_task(worker._batch_loop())
    for _ in range(200):
        if worker._consumer.commits:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0.01)
    worker._stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert calls["n"] >= 3  # failed twice, task kept going
    assert worker._consumer.commits == [{(TOPIC, 0): 8}]  # type: ignore[attr-defined]
