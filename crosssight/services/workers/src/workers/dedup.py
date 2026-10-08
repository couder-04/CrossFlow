"""Duplicate read detection for ingest worker."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

DEDUP_WINDOW_SEC = 10.0


@dataclass(frozen=True)
class DedupKey:
    plate_norm: str
    camera_id: str


class InMemoryDedup:
    """LRU-style in-memory dedup with 10s window per plate+camera."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._max_entries = max_entries
        self._seen: OrderedDict[DedupKey, float] = OrderedDict()

    def _prune_expired(self, now: float) -> None:
        """Drop entries older than the window.

        The dict is insertion-ordered, not timestamp-ordered, so a prefix scan
        misses stale keys inserted during backfill or replay. Scan every entry,
        capped at max_entries so the work stays bounded.
        """
        cutoff = now - DEDUP_WINDOW_SEC
        stale: list[DedupKey] = []
        for index, (key, ts) in enumerate(self._seen.items()):
            if index >= self._max_entries:
                break
            if ts < cutoff:
                stale.append(key)
        for key in stale:
            self._seen.pop(key, None)

    def is_duplicate(self, plate_norm: str, camera_id: str, ts_epoch: float | None = None) -> bool:
        now = ts_epoch if ts_epoch is not None else time.time()
        self._prune_expired(now)
        key = DedupKey(plate_norm=plate_norm.upper(), camera_id=camera_id)
        last = self._seen.get(key)
        if last is not None and (now - last) < DEDUP_WINDOW_SEC:
            return True
        self._seen[key] = now
        if len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return False

    def clear(self) -> None:
        self._seen.clear()


class RedisDedup:
    """Redis-backed dedup using SET NX with TTL."""

    def __init__(self, redis, prefix: str = "dedup") -> None:
        self.redis = redis
        self.prefix = prefix

    def _key(self, plate_norm: str, camera_id: str) -> str:
        return f"{self.prefix}:{plate_norm.upper()}:{camera_id}"

    async def is_duplicate(self, plate_norm: str, camera_id: str) -> bool:
        key = self._key(plate_norm, camera_id)
        created = await self.redis.set(key, "1", nx=True, ex=int(DEDUP_WINDOW_SEC))
        return created is None


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
        if self.memory.is_duplicate(plate_norm, camera_id, ts_epoch):
            return True
        if self.redis is not None:
            return await self.redis.is_duplicate(plate_norm, camera_id)
        return False
