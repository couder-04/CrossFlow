"""The retention statement uses RAW_RETENTION_DAYS, not a fixed 30."""

from api.clickhouse_retention import retention_sql


def test_retention_sql_uses_configured_days():
    statement = retention_sql(14)
    assert "INTERVAL 14 DAY" in statement
    assert "ALTER TABLE anpr_reads MODIFY TTL" in statement
