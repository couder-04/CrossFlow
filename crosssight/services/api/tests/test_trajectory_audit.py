"""A widened trajectory search must leave a second audit row."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.auth import Role, UserContext
from api.deps import get_clickhouse, get_current_user, get_session
from api.main import create_app
from fastapi.testclient import TestClient

USER_ID = "11111111-1111-1111-1111-111111111111"


class _Session:
    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None


class _ClickHouse:
    def query(self, sql: str, parameters: dict | None = None):
        return SimpleNamespace(result_rows=[])


def test_widened_trajectory_writes_two_audit_rows():
    session = _Session()
    ch = _ClickHouse()

    async def _session():
        yield session

    with (
        patch("api.main.ensure_seed_users", new=AsyncMock()),
        patch("api.main.apply_postgres_upgrade", new=AsyncMock()),
    ):
        app = create_app()
        app.dependency_overrides[get_clickhouse] = lambda: ch
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            id=USER_ID, username="operator", role=Role.operator
        )
        client = TestClient(app)
        resp = client.get("/trajectory", params={"plate": "BR01AB1234", "case_id": "CASE-1"})

    assert resp.status_code == 200
    assert resp.json()["summary"]["data_status"] == "widened_to_all_history"
    assert len(session.rows) == 2
    narrow, wide = session.rows
    assert narrow.action == "trajectory_query"
    assert wide.action == "trajectory_query_widened"
    assert wide.params["reason"] == "empty_result_fallback"
    assert wide.params["from"].startswith("2000-01-01")
    assert narrow.params["from"] != wide.params["from"]
    assert narrow.params["to"] != wide.params["to"]
