"""Camera OD unique_vehicles must not echo trip_count, and must respect k-anonymity."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from anpr_common.config import get_settings
from api.auth import Role, UserContext
from api.deps import get_clickhouse, get_current_user, get_session, get_settings_dep
from api.main import create_app
from fastapi.testclient import TestClient


class _Rows:
    def all(self):
        return []


class _Session:
    async def execute(self, *_args, **_kwargs):
        return _Rows()


class FakeClickHouse:
    """Returns seeded rows for the rollup query and the plate-cardinality query."""

    def __init__(self, rollup: list[tuple], vehicles: list[tuple]) -> None:
        self.rollup = rollup
        self.vehicles = vehicles
        self.queries: list[str] = []

    def query(self, sql: str, parameters: dict | None = None):
        self.queries.append(sql)
        if "od_camera_hourly" in sql:
            return SimpleNamespace(result_rows=self.rollup)
        if "uniqExact" in sql:
            return SimpleNamespace(result_rows=self.vehicles)
        raise AssertionError(f"unexpected query: {sql}")


@contextmanager
def _client(
    k: int, rollup: list[tuple], vehicles: list[tuple]
) -> Iterator[tuple[TestClient, FakeClickHouse]]:
    ch = FakeClickHouse(rollup, vehicles)
    settings = get_settings().model_copy(update={"od_k_anon": k})

    async def _session():
        yield _Session()

    with (
        patch("api.main.ensure_seed_users", new=AsyncMock()),
        patch("api.main.apply_postgres_upgrade", new=AsyncMock()),
    ):
        app = create_app()
        app.dependency_overrides[get_clickhouse] = lambda: ch
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_settings_dep] = lambda: settings
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            id="admin-id", username="admin", role=Role.admin
        )
        yield TestClient(app), ch


def test_rollup_unique_vehicles_is_not_trip_count():
    with _client(k=3, rollup=[("cam-a", "cam-b", 5)], vehicles=[("cam-a", "cam-b", 3)]) as (
        client,
        ch,
    ):
        resp = client.get("/ops/od")
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert len(cells) == 1
    assert cells[0]["trip_count"] == 5
    assert cells[0]["unique_vehicles"] == 3
    assert any("uniqExact" in sql for sql in ch.queries)


def test_unique_vehicles_below_k_anon_suppresses_cell():
    with _client(k=5, rollup=[("cam-a", "cam-b", 6)], vehicles=[("cam-a", "cam-b", 4)]) as (
        client,
        ch,
    ):
        resp = client.get("/ops/od")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cells"] == []
    assert body["k_anonymity"] == 5
    assert any("uniqExact" in sql for sql in ch.queries)
