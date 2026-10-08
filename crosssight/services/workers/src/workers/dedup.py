"""Duplicate read detection for ingest worker."""

from __future__ import annotations

import heapq
import itertools
import time
from collections import OrderedDict
from dataclasses import dataclass

DEDUP_WINDOW_SEC = 10.0
# Redis keys hold the last accepted event time. The TTL only bounds memory; it is generous
# so that lagging consumers still compare against the previous read.
REDIS_DEDUP_TTL_SEC = 3600


@dataclass(frozen=True)
class DedupKey:
    plate_norm: str
    camera_id: str


class InMemoryDedup:
    """LRU-bounded in-memory dedup with a 10s event-time window per plate+camera."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._max_entries = max_entries
        self._seen: OrderedDict[DedupKey, float] = OrderedDict()
        # Min-heap of (ts, seq, key) so expired entries are found without scanning _seen. An
        # entry is live only while _seen still maps its key to that ts; refreshed or evicted
        # keys leave stale heap entries that are skipped when popped. seq breaks ts ties.
        self._heap: list[tuple[float, int, DedupKey]] = []
        self._seq = itertools.count()

    def _prune_expired(self, now: float) -> None:
        """Drop entries older than the window; amortised O(log n) per call."""
        cutoff = now - DEDUP_WINDOW_SEC
        heap = self._heap
        while heap and heap[0][0] < cutoff:
            ts, _, key = heapq.heappop(heap)
            if self._seen.get(key) == ts:
                del self._seen[key]

    def _remember(self, key: DedupKey, ts: float) -> None:
        self._seen[key] = ts
        self._seen.move_to_end(key)
        heapq.heappush(self._heap, (ts, next(self._seq), key))
        if len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        if len(self._heap) > 2 * self._max_entries:
            # Evicted/refreshed keys left too many stale entries: rebuild from the live ones.
            self._heap = [(t, next(self._seq), k) for k, t in self._seen.items()]
            heapq.heapify(self._heap)

    def is_duplicate(self, plate_norm: str, camera_id: str, ts_epoch: float | None = None) -> bool:
        now = ts_epoch if ts_epoch is not None else time.time()
        self._prune_expired(now)
        key = DedupKey(plate_norm=plate_norm.upper(), camera_id=camera_id)
        last = self._seen.get(key)
        if last is not None and abs(now - last) < DEDUP_WINDOW_SEC:
            return True
        self._remember(key, now)
        return False

    def clear(self) -> None:
        self._seen.clear()
        self._heap.clear()


# Atomic compare-and-set on event time: duplicate iff |ts - last_ts| < window. The stored
# value is the event time of the last accepted read, not a wall-clock TTL, so replay and
# backfill (old timestamps arriving now) are judged the same way as live traffic.
_REDIS_DEDUP_LUA = """
local last = redis.call('GET', KEYS[1])
if last and math.abs(tonumber(ARGV[1]) - tonumber(last)) < tonumber(ARGV[2]) then
  return 1
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
return 0
"""


class RedisDedup:
    """Redis-backed dedup keyed by plate+camera, comparing event timestamps."""

    def __init__(self, redis, prefix: str = "dedup") -> None:
        self.redis = redis
        self.prefix = prefix

    def _key(self, plate_norm: str, camera_id: str) -> str:
        return f"{self.prefix}:{plate_norm.upper()}:{camera_id}"

    async def is_duplicate(
        self, plate_norm: str, camera_id: str, ts_epoch: float | None = None
    ) -> bool:
        ts = ts_epoch if ts_epoch is not None else time.time()
        duplicate = await self.redis.eval(
            _REDIS_DEDUP_LUA,
            1,
            self._key(plate_norm, camera_id),
            repr(float(ts)),
            repr(DEDUP_WINDOW_SEC),
            REDIS_DEDUP_TTL_SEC,
        )
        return bool(int(duplicate))


class HybridDedup:
    """Fast in-memory check plus Redis for cross-replica consistency."""

    def __init__(self, memory: InMemoryDedup, redis_dedup: RedisDedup | None) -> None:
        self.memory = memory
        self.redis = redis_dedup

    async def is_duplicate(
        self,
        plate_norm: str,
        camera_id: str,
        ts_epoch: float | None = None,
    ) -> bool:
        ts = ts_epoch if ts_epoch is not None else time.time()
        if self.memory.is_duplicate(plate_norm, camera_id, ts):
            return True
        if self.redis is not None:
            return await self.redis.is_duplicate(plate_norm, camera_id, ts)
        return False
