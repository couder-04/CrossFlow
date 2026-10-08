"""Alert engine: run rules, dedupe, persist, publish."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
from collections.abc import Sequence
from datetime import UTC
from typing import Any

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from anpr_common.config import Settings, get_settings
from anpr_common.schemas import Alert, PlateRead
from pydantic import ValidationError

from workers.alerts.registry import RegistryClient, build_registry_client
from workers.alerts.rules import (
    ALERT_DEDUP_MINUTES,
    AlertDeduper,
    ClonedPlateRule,
    ConvoyRule,
    DefaultRuleContext,
    GeofenceRule,
    LoiteringRule,
    PlateVehicleMismatchRule,
    RedisAlertDeduper,
    RouteAnomalyRule,
    Rule,
    StoppedVehicleRule,
    WatchlistRule,
    WrongWayRule,
)
from workers.db import (
    create_pg_pool,
    create_redis,
    load_camera_pairs,
    load_cameras,
    load_dwell_overrides,
    load_watchlist,
    load_zone_membership,
    persist_alert,
)
from workers.health import HealthServer

logger = logging.getLogger(__name__)

ZONE_CACHE_REFRESH_SEC = 60
WATCHLIST_REFRESH_SEC = 5
CONVOY_ZSET_TTL_SEC = 1800


async def _is_duplicate(deduper: AlertDeduper | RedisAlertDeduper, alert: Alert, now) -> bool:
    seen = deduper.is_duplicate(alert, now)
    if inspect.isawaitable(seen):
        seen = await seen
    return bool(seen)


class AlertEngine:
    def __init__(
        self,
        rules: Sequence[Rule],
        ctx: DefaultRuleContext,
        deduper: AlertDeduper | RedisAlertDeduper | None = None,
    ) -> None:
        self.rules = list(rules)
        self.ctx = ctx
        self.deduper = deduper or AlertDeduper()

    async def process(self, read: PlateRead) -> list[Alert]:
        # Evaluate rules concurrently — they only read shared ctx/redis
        results = await asyncio.gather(
            *[rule.evaluate(read, self.ctx) for rule in self.rules],
            return_exceptions=True,
        )
        alerts: list[Alert] = []
        for rule, result in zip(self.rules, results, strict=True):
            if isinstance(result, BaseException):
                logger.exception("Rule %s failed", rule.name, exc_info=result)
                continue
            for alert in result:
                if not await _is_duplicate(self.deduper, alert, read.ts.replace(tzinfo=None)):
                    alerts.append(alert)
        return alerts


def build_rules(registry: RegistryClient | None = None) -> list[Rule]:
    reg = registry or build_registry_client(None)
    return [
        WatchlistRule(),
        ClonedPlateRule(),
        ConvoyRule(),
        LoiteringRule(),
        GeofenceRule(),
        WrongWayRule(),
        PlateVehicleMismatchRule(reg),
        RouteAnomalyRule(),
        StoppedVehicleRule(),
    ]


class AlertsWorker:
    def __init__(
        self,
        settings: Settings | None = None,
        health_port: int | None = None,
        registry: RegistryClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.health_port = health_port or int(os.environ.get("HEALTH_PORT", "8083"))
        self.registry = registry or build_registry_client(self.settings.registry_path)
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._health = HealthServer("alerts", self.health_port, ready_check=self._ready)
        self._stop: asyncio.Event | None = None
        self._engine: AlertEngine | None = None
        self._redis: Any | None = None
        self._pg_pool = None
        self._ctx: DefaultRuleContext | None = None
        self._ready_flag = False
        self._processed = 0
        self._alerts_emitted = 0

    def _ready(self) -> bool:
        return self._ready_flag

    async def _init(self) -> None:
        self._pg_pool = await create_pg_pool(self.settings)
        cameras = await load_cameras(self._pg_pool)
        pairs = await load_camera_pairs(self._pg_pool, adjacent_only=False)
        watchlist = await load_watchlist(self._pg_pool)
        sensitive, restricted = await load_zone_membership(self._pg_pool)
        dwell_overrides = await load_dwell_overrides(self._pg_pool)
        self._redis = await create_redis(self.settings)
        ctx = DefaultRuleContext(
            self.settings,
            cameras,
            watchlist,
            pairs,
            self._redis,
            self._pg_pool,
            sensitive_zones=sensitive,
            restricted_zones=restricted,
            dwell_overrides=dwell_overrides,
        )
        if self.settings.redis_url and self._redis is not None:
            deduper: AlertDeduper | RedisAlertDeduper = RedisAlertDeduper(self._redis)
        else:
            logger.warning(
                "Redis unavailable; alert dedupe is in-process and will not span replicas"
            )
            deduper = AlertDeduper()
        self._engine = AlertEngine(build_rules(self.registry), ctx, deduper=deduper)
        self._ctx = ctx
        logger.info(
            "Alerts ready: cameras=%d watchlist=%d sensitive_cams=%d restricted_cams=%d",
            len(cameras),
            len(watchlist),
            len(sensitive),
            len(restricted),
        )

    async def _reload_loop(self) -> None:
        """Refresh watchlist + zone cache without process restart."""
        assert self._pg_pool is not None and self._ctx is not None
        ticks = 0
        while self._stop is not None and not self._stop.is_set():
            try:
                self._ctx.watchlist = await load_watchlist(self._pg_pool)
                if ticks % max(1, ZONE_CACHE_REFRESH_SEC // WATCHLIST_REFRESH_SEC) == 0:
                    sensitive, restricted = await load_zone_membership(self._pg_pool)
                    self._ctx.set_zone_cache(sensitive, restricted)
                    logger.debug(
                        "Zone cache refreshed sensitive=%d restricted=%d",
                        len(sensitive),
                        len(restricted),
                    )
            except Exception:
                logger.exception("alerts reload failed")
            ticks += 1
            await asyncio.sleep(WATCHLIST_REFRESH_SEC)

    async def _redis_dedupe(self, alert: Alert) -> bool:
        """Cross-replica dedupe (True = duplicate, skip)."""
        assert self._redis is not None
        key = f"alert:dedup:{alert.type.value}:{alert.plate_norm.upper()}"
        created = await self._redis.set(key, "1", nx=True, ex=ALERT_DEDUP_MINUTES * 60)
        return created is None

    async def _publish_alert(self, alert: Alert) -> None:
        assert self._producer is not None and self._redis is not None and self._pg_pool
        assert self._engine is not None
        # RedisAlertDeduper already claimed the key in process(); a second SET NX
        # would see that key and drop the alert.
        already_shared = isinstance(self._engine.deduper, RedisAlertDeduper)
        if not already_shared and await self._redis_dedupe(alert):
            return
        payload = alert.model_dump_json().encode()
        await self._producer.send_and_wait(self.settings.topic_alerts, payload)
        await self._redis.publish("alerts", alert.model_dump_json())
        await persist_alert(
            self._pg_pool,
            {
                "id": str(alert.id),
                "type": alert.type.value,
                "severity": alert.severity.value,
                "plate_norm": alert.plate_norm,
                "camera_ids": alert.camera_ids,
                "evidence": alert.evidence,
                "status": alert.status.value,
                "needs_verification": alert.needs_verification,
                "ts": alert.ts,
            },
        )
        self._alerts_emitted += 1

    async def _prime_convoy_state(self, read: PlateRead) -> None:
        assert self._redis is not None
        plate = read.plate_norm.upper()
        ts_epoch = read.ts.timestamp()
        pipe = self._redis.pipeline(transaction=False)
        pipe.zadd(f"convoy:cam:{read.camera_id}", {plate: ts_epoch})
        pipe.expire(f"convoy:cam:{read.camera_id}", CONVOY_ZSET_TTL_SEC)
        # Plate→camera index for O(|shared|) convoy checks instead of O(|cameras|)
        pipe.zadd(f"convoy:plate:{plate}", {read.camera_id: ts_epoch})
        pipe.expire(f"convoy:plate:{plate}", CONVOY_ZSET_TTL_SEC)
        await pipe.execute()

    async def _record_last_seen(self, read: PlateRead) -> None:
        assert self._redis is not None
        plate = read.plate_norm.upper()
        await self._redis.hset(
            f"alerts:lastseen:{plate}",
            mapping={
                "camera_id": read.camera_id,
                "ts": read.ts.replace(tzinfo=UTC).isoformat()
                if read.ts.tzinfo is None
                else read.ts.isoformat(),
                "confidence": str(read.confidence),
            },
        )

    async def run(self) -> None:
        logging.basicConfig(level=logging.INFO)
        self._stop = asyncio.Event()
        await self._health.start()
        await self._init()
        self._ready_flag = True

        group = os.environ.get("ALERTS_GROUP_ID", "anpr-alerts")
        self._consumer = AIOKafkaConsumer(
            self.settings.topic_reads,
            bootstrap_servers=self.settings.kafka_bootstrap,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="latest",
            max_poll_records=100,
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap,
            linger_ms=5,
        )
        await self._consumer.start()
        await self._producer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        assert self._engine is not None
        reload_task = asyncio.create_task(self._reload_loop())
        logger.info("Alerts worker consuming group=%s", group)
        try:
            async for msg in self._consumer:
                if self._stop.is_set():
                    break
                try:
                    read = PlateRead.model_validate(orjson.loads(msg.value))
                except (ValidationError, orjson.JSONDecodeError):
                    continue
                await self._prime_convoy_state(read)
                for alert in await self._engine.process(read):
                    await self._publish_alert(alert)
                await self._record_last_seen(read)
                self._processed += 1
        finally:
            self._ready_flag = False
            self._health.set_healthy(False)
            reload_task.cancel()
            try:
                await reload_task
            except asyncio.CancelledError:
                pass
            if self._consumer:
                await self._consumer.stop()
            if self._producer:
                await self._producer.stop()
            if self._pg_pool:
                await self._pg_pool.close()
            if self._redis:
                await self._redis.aclose()
            await self._health.stop()
            logger.info(
                "Alerts worker stopped processed=%d emitted=%d",
                self._processed,
                self._alerts_emitted,
            )
