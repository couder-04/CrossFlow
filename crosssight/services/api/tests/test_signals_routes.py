"""/signals endpoints: published results, latest run, and camera-measured demand."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.auth import Role, UserContext
from api.deps import get_clickhouse, get_current_user
from api.main import create_app
from fastapi.testclient import TestClient

pytest.importorskip("crossflow")


class FakeCH:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def query(self, sql, parameters=None):
        self.calls.append(parameters or {})
        return SimpleNamespace(result_rows=self.rows)


@pytest.fixture
def client_factory():
    def make(rows=()):
        with patch("api.main.ensure_seed_users", new=AsyncMock()):
            app = create_app()
        ch = FakeCH(list(rows))
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            id="1", username="analyst", role=Role.analyst
        )
        app.dependency_overrides[get_clickhouse] = lambda: ch
        return TestClient(app, raise_server_exceptions=False), ch

    return make


def test_requires_login():
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        app = create_app()
    assert TestClient(app).get("/signals/summary").status_code == 401


def test_summary_serves_the_published_results(client_factory):
    client, _ = client_factory()
    body = client.get("/signals/summary").json()
    assert body["available"] is True
    assert {h["scenario"] for h in body["headlines"]} == {
        "balanced",
        "peak_unbalanced",
        "dynamic",
        "low_demand",
    }
    assert "best baseline" in body["note"]


def test_summary_degrades_when_results_are_missing(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFLOW_XTRAFLOW_RESULTS", str(tmp_path / "none"))
    monkeypatch.setenv("CROSSFLOW_RUNS_DIR", str(tmp_path / "runs"))
    client, _ = client_factory()
    resp = client.get("/signals/summary")
    assert resp.status_code == 200
    assert resp.json()["available"] is False and resp.json()["latest_run"] is None


def test_latest_run_without_runs_is_not_an_error(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFLOW_RUNS_DIR", str(tmp_path))
    client, _ = client_factory()
    assert client.get("/signals/runs/latest").json()["available"] is False


def test_latest_run_drops_raw_rows(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSFLOW_RUNS_DIR", str(tmp_path))
    d = tmp_path / "20260101T000000Z"
    d.mkdir()
    (d / "report.json").write_text(
        json.dumps({"created": "x", "comparison": {"verdict": "v"}, "raw_runs": [{"a": 1}]})
    )
    client, _ = client_factory()
    rep = client.get("/signals/runs/latest").json()["report"]
    assert rep["comparison"]["verdict"] == "v" and "raw_runs" not in rep


Q = {"from": "2026-01-12T08:00:00", "to": "2026-01-12T09:00:00"}


def test_demand_divides_by_the_period_not_by_rows(client_factory):
    # cam-N saw 480 vehicles in the hour; cam-E saw 120 in the hour
    rows = [
        ("cam-N", 300, 100, 20, 40, 20, 0, 480),
        ("cam-E", 100, 10, 0, 10, 0, 0, 120),
    ]
    client, ch = client_factory(rows)
    body = client.get("/signals/demand", params={"cameras": "cam-N:N,cam-E:E", **Q}).json()
    assert body["approach_veh_h"] == {"N": 480.0, "E": 120.0}
    assert body["scale_vs_xtraflow_balanced"]["N"] == 1.0
    assert body["period_hours"] == 1.0
    assert ch.calls[0]["ids"] == ["cam-N", "cam-E"]
    assert sum(body["vehicle_mix"].values()) == pytest.approx(1.0, abs=1e-3)


def test_silent_camera_is_zero_not_missing(client_factory):
    client, _ = client_factory([("cam-N", 10, 0, 0, 0, 0, 0, 10)])
    body = client.get("/signals/demand", params={"cameras": "cam-N:N,cam-S:S", **Q}).json()
    assert body["approach_veh_h"]["S"] == 0.0
    assert body["silent_cameras"] == ["cam-S"]


def test_two_cameras_on_one_arm_add(client_factory):
    rows = [("a", 50, 0, 0, 0, 0, 0, 50), ("b", 70, 0, 0, 0, 0, 0, 70)]
    client, _ = client_factory(rows)
    body = client.get("/signals/demand", params={"cameras": "a:W,b:W", **Q}).json()
    assert body["approach_veh_h"] == {"W": 120.0}


@pytest.mark.parametrize("cams", ["", "cam-N", "cam-N:Q", ":N", "cam-N:N,bad"])
def test_bad_camera_spec_is_rejected(client_factory, cams):
    client, _ = client_factory()
    assert client.get("/signals/demand", params={"cameras": cams, **Q}).status_code == 422


def test_bad_period_is_rejected(client_factory):
    client, _ = client_factory()
    reversed_q = {"from": Q["to"], "to": Q["from"]}
    assert client.get("/signals/demand", params={"cameras": "a:N", **reversed_q}).status_code == 422
    long_q = {"from": "2026-01-01T00:00:00", "to": "2026-03-01T00:00:00"}
    assert client.get("/signals/demand", params={"cameras": "a:N", **long_q}).status_code == 422
    assert datetime.fromisoformat(Q["to"]) > datetime.fromisoformat(Q["from"])
