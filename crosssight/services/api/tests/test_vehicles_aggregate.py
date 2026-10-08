"""/ops/vehicles aggregates grouped counts without expanding one dict per read."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from anpr_common.intelligence.journeys import (
    aggregate_vehicle_class_counts,
    aggregate_vehicle_classes,
)
from api.auth import Role, UserContext
from api.deps import get_clickhouse, get_current_user
from api.main import create_app
from fastapi.testclient import TestClient


class FakeCH:
    def __init__(self, grouped_rows, trend_rows=()) -> None:
        self.grouped_rows = list(grouped_rows)
        self.trend_rows = list(trend_rows)

    def query(self, sql, parameters=None):
        rows = self.trend_rows if "toStartOfHour" in sql else self.grouped_rows
        return SimpleNamespace(result_rows=rows)


def _client(ch: FakeCH) -> TestClient:
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        app = create_app()
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        id="1", username="analyst", role=Role.analyst
    )
    app.dependency_overrides[get_clickhouse] = lambda: ch
    return TestClient(app, raise_server_exceptions=False)


def test_vehicles_with_millions_of_reads_returns_quickly():
    ch = FakeCH([("car", "cam-1", 5_000_000)], [("2026-01-01 00:00:00", "car", 5_000_000)])
    started = time.perf_counter()
    resp = _client(ch).get("/ops/vehicles")
    elapsed = time.perf_counter() - started
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5_000_000
    assert body["counts"] == {"car": 5_000_000}
    assert body["shares"] == {"car": 1.0}
    assert body["by_camera"] == {"cam-1": {"car": 5_000_000}}
    assert body["trend"][0]["count"] == 5_000_000
    assert elapsed < 0.5, f"took {elapsed:.2f}s: reads were probably materialised"


def test_vehicles_totals_and_shares_across_rows():
    ch = FakeCH([("car", "cam-1", 30), ("truck", "cam-1", 10), ("car", "cam-2", 60)])
    body = _client(ch).get("/ops/vehicles").json()
    assert body["total"] == 100
    assert body["counts"] == {"car": 90, "truck": 10}
    assert body["shares"]["truck"] == 0.1
    assert body["by_camera"]["cam-2"] == {"car": 60}


def test_count_rows_match_expanded_rows():
    grouped = [("car", "cam-a", 3), ("truck", "cam-b", 2), ("", "cam-a", 1), ("car", None, 1)]
    expanded = [
        {"vehicle_class": klass, "camera_id": cam}
        for klass, cam, count in grouped
        for _ in range(count)
    ]
    assert aggregate_vehicle_class_counts(grouped) == aggregate_vehicle_classes(expanded)
