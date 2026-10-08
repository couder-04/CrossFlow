"""Blocking ClickHouse calls must never run on the event loop thread."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.auth import Role, UserContext
from api.ch import ch_command, ch_query
from api.deps import get_clickhouse, get_current_user
from api.main import create_app
from fastapi.testclient import TestClient

pytest.importorskip("crossflow")  # /signals/demand is only mounted with the signal-study package

FROM_TO = {"from": "2026-01-01T00:00:00", "to": "2026-01-01T01:00:00"}

ENDPOINTS = [
    ("/ops/vehicles", {}),
    ("/analytics/flow", {"camera_id": "cam-1", **FROM_TO}),
    ("/analytics/segments", {"at": "2026-01-01T00:00:00"}),
    ("/analytics/bottlenecks", {}),
    ("/analytics/anomalies", {}),
    ("/analytics/route-density", {}),
    ("/analytics/heatmap", {}),
    ("/analytics/heatmap", {"source": "video"}),
    ("/analytics/od", {"hour": 3, "date": "2026-01-01"}),
    ("/signals/demand", {"cameras": "cam-1:N", **FROM_TO}),
]


class FakeCH:
    """Empty results; remembers whether each call ran on a thread with a running event loop."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.on_loop: list[bool] = []

    def _record(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.on_loop.append(False)
        else:
            self.on_loop.append(True)

    def query(self, sql, parameters=None):
        self._record()
        if self.error:
            raise self.error
        return SimpleNamespace(result_rows=[])

    def command(self, sql, parameters=None):
        self._record()
        return "ok"


def _client(ch: FakeCH) -> TestClient:
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        app = create_app()
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        id="1", username="analyst", role=Role.analyst
    )
    app.dependency_overrides[get_clickhouse] = lambda: ch
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path,params", ENDPOINTS)
def test_endpoint_queries_run_off_the_event_loop(path, params):
    ch = FakeCH()
    resp = _client(ch).get(path, params=params)
    assert resp.status_code == 200, resp.text
    assert ch.on_loop, "endpoint made no ClickHouse call"
    assert not any(ch.on_loop), "ClickHouse was called on the event loop thread"


async def test_helpers_run_off_the_loop_and_pass_arguments_through():
    ch = FakeCH()
    assert (await ch_query(ch, "SELECT 1")).result_rows == []
    assert (await ch_query(ch, "SELECT 1", {"a": 1})).result_rows == []
    assert await ch_command(ch, "ALTER TABLE t") == "ok"
    assert ch.on_loop == [False, False, False]


async def test_helper_propagates_errors():
    with pytest.raises(RuntimeError, match="boom"):
        await ch_query(FakeCH(RuntimeError("boom")), "SELECT 1")


def test_ops_endpoints_still_map_failures_to_503():
    resp = _client(FakeCH(RuntimeError("ch down"))).get("/ops/vehicles")
    assert resp.status_code == 503
    assert "analytics store unavailable" in resp.json()["detail"]


def test_no_direct_clickhouse_calls_outside_the_helper():
    """New code must go through api.ch; the retention helper is sync and runs in a thread."""
    import re
    from pathlib import Path

    import api

    src = Path(api.__file__).parent
    direct = re.compile(r"\b(?:ch|client)\.(?:query|command|insert)\(")
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in src.rglob("*.py")
        if path.name not in {"ch.py", "clickhouse_retention.py"}
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if direct.search(line)
    ]
    assert offenders == []
