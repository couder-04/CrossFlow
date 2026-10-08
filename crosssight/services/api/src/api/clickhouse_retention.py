"""Apply RAW_RETENTION_DAYS to the raw reads table.

Docker's init scripts do not substitute environment variables, so the
checked-in TTL is only the first-boot default. The API restates it on startup.
"""

from __future__ import annotations

import logging

import clickhouse_connect
from anpr_common.config import Settings

logger = logging.getLogger(__name__)


def retention_sql(days: int) -> str:
    safe_days = int(days)
    if safe_days < 1:
        raise ValueError("RAW_RETENTION_DAYS must be at least 1")
    return f"ALTER TABLE anpr_reads MODIFY TTL toDateTime(ts) + INTERVAL {safe_days} DAY"


def apply_clickhouse_retention(client, days: int) -> None:
    client.command(retention_sql(days))


def apply_clickhouse_retention_from_settings(settings: Settings) -> None:
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database=settings.clickhouse_db,
        connect_timeout=2,
    )
    try:
        apply_clickhouse_retention(client, settings.raw_retention_days)
    finally:
        client.close()
    logger.info("ClickHouse anpr_reads TTL is %s days", int(settings.raw_retention_days))
