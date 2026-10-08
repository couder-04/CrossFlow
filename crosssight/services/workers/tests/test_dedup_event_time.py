"""Dedup prunes in amortised O(log n) and judges Redis duplicates by event time."""

from __future__ import annotations

import time

import pytest
from workers.dedup import (
    DEDUP_WINDOW_SEC,
    DedupKey,
    HybridDedup,
    InMemoryDedup,
    RedisDedup,
)


class FakeRedis:
    """Emulates the dedup Lua script's contract: KEYS[1], ARGV = ts, window, ttl."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.ttls: dict[str, int] = {}
        self.calls = 0

    async def eval(self, script, numkeys, key, ts, window, ttl):
        assert numkeys == 1 and "math.abs" in script
        self.calls += 1
        ts, window = float(ts), float(window)
        last = self.store.get(key)
        if last is not None and abs(ts - last) < window:
            return 1
        self.store[key] = ts
        self.ttls[key] = int(ttl)
        return 0


def test_in_memory_pruning_is_amortised():
    dedup = InMemoryDedup(max_entries=100_000)
    t0 = 1_000_000.0
    for i in range(100_000):
        dedup.is_duplicate(f"P{i}", "cam-1", t0 + i * 1e-5)  # all inside one window
    assert len(dedup._seen) == 100_000

    started = time.perf_counter()
    for i in range(10_000):
        dedup.is_duplicate(f"Q{i}", "cam-1", t0 + 1 + i * 1e-5)
    elapsed = time.perf_counter() - started
    # The old per-call scan of every entry took minutes here; this is milliseconds.
    assert elapsed < 3.0, f"10k calls over 100k entries took {elapsed:.2f}s"
    assert len(dedup._seen) == 100_000  # max_entries still enforced (LRU eviction)


def test_in_memory_expired_entries_are_dropped_and_heap_stays_bounded():
    dedup = InMemoryDedup(max_entries=1_000)
    for i in range(5_000):
        dedup.is_duplicate(f"P{i}", "cam-1", 100.0)  # constant ts: nothing ever expires
    assert len(dedup._seen) == 1_000
    assert len(dedup._heap) <= 2_000 + 1
    # Moving event time past the window drops them all.
    dedup.is_duplicate("LATE", "cam-1", 100.0 + DEDUP_WINDOW_SEC + 1)
    assert list(dedup._seen) == [DedupKey("LATE", "cam-1")]


def test_in_memory_lru_evicts_least_recently_updated():
    dedup = InMemoryDedup(max_entries=2)
    dedup.is_duplicate("A", "c", 1.0)
    dedup.is_duplicate("B", "c", 1.0)
    dedup.is_duplicate("A", "c", 100.0)  # window passed: A refreshed, so B is now the oldest
    dedup.is_duplicate("C", "c", 100.0)
    assert {k.plate_norm for k in dedup._seen} == {"A", "C"}


async def test_redis_dedup_is_event_time_aware():
    redis = FakeRedis()
    dedup = RedisDedup(redis)
    t0 = 1_700_000_000.0
    assert await dedup.is_duplicate("MH12AB1234", "cam-1", t0) is False
    assert await dedup.is_duplicate("MH12AB1234", "cam-1", t0 + 3) is True
    assert await dedup.is_duplicate("MH12AB1234", "cam-1", t0 - 3) is True  # out of order
    assert await dedup.is_duplicate("MH12AB1234", "cam-2", t0 + 3) is False
    assert await dedup.is_duplicate("MH12AB1234", "cam-1", t0 + DEDUP_WINDOW_SEC + 0.5) is False
    assert max(redis.ttls.values()) > 60  # generous TTL, not the 10 s window


@pytest.mark.parametrize("with_redis", [True, False])
async def test_replay_one_hour_apart_keeps_both_reads(with_redis):
    redis = FakeRedis()
    hybrid = HybridDedup(InMemoryDedup(), RedisDedup(redis) if with_redis else None)
    t0 = 1_700_000_000.0
    # Back-to-back in wall-clock time, an hour apart in event time (replay/backfill).
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0) is False
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0 + 3600) is False
    # 3 s apart in event time is still a duplicate.
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0 + 3603) is True


async def test_redis_decides_across_replicas_with_event_time():
    redis = FakeRedis()
    left = HybridDedup(InMemoryDedup(), RedisDedup(redis))
    right = HybridDedup(InMemoryDedup(), RedisDedup(redis))
    t0 = 1_700_000_000.0
    assert await left.is_duplicate("DL3CAB1234", "cam-1", t0) is False
    assert await right.is_duplicate("DL3CAB1234", "cam-1", t0 + 3) is True
    assert await right.is_duplicate("DL3CAB1234", "cam-1", t0 + 3600) is False


async def test_hybrid_passes_event_time_to_redis():
    seen: list[float | None] = []

    class Spy:
        async def is_duplicate(self, plate, camera, ts_epoch=None):
            seen.append(ts_epoch)
            return False

    hybrid = HybridDedup(InMemoryDedup(), Spy())  # type: ignore[arg-type]
    await hybrid.is_duplicate("A1", "cam", 1234.5)
    assert seen == [1234.5]
