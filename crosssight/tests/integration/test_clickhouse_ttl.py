"""RAW_RETENTION_DAYS is the TTL on anpr_reads. Requires a running ClickHouse."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to check ClickHouse TTL",
)


def test_anpr_reads_ttl_matches_raw_retention_days():
    import clickhouse_connect
    from anpr_common.config import get_settings
    from api.clickhouse_retention import apply_clickhouse_retention

    settings = get_settings()
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database=settings.clickhouse_db,
    )
    try:
        apply_clickhouse_retention(client, settings.raw_retention_days)
        create = client.command("SHOW CREATE TABLE anpr_reads")
    finally:
        client.close()
    days = int(settings.raw_retention_days)
    # ClickHouse echoes the TTL as written (INTERVAL n DAY) or normalised (toIntervalDay(n)).
    assert f"INTERVAL {days} DAY" in str(create) or f"toIntervalDay({days})" in str(create)
