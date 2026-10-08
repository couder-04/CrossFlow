"""/sources/mode: only operators start/stop the runner, and no server paths leak."""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest
from api import video_feeds
from api.auth import Role, UserContext
from api.deps import get_current_user
from api.main import create_app
from fastapi.testclient import TestClient


def _client(role: Role) -> TestClient:
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        app = create_app()
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        id="1", username=role.value, role=role
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("mode", ["sim", "video"])
def test_analyst_cannot_switch_source_mode(mode, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_FEEDS_DIR", str(tmp_path))
    with (
        patch("api.routes.sources.stop_runner") as stop,
        patch("api.routes.sources.start_runner") as start,
    ):
        resp = _client(Role.analyst).post("/sources/mode", json={"mode": mode})
    assert resp.status_code == 403
    stop.assert_not_called()
    start.assert_not_called()


def test_operator_can_stop_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_FEEDS_DIR", str(tmp_path))
    with patch("api.routes.sources.stop_runner") as stop:
        resp = _client(Role.operator).post("/sources/mode", json={"mode": "sim"})
    assert resp.status_code == 200
    stop.assert_called_once()


def test_get_mode_does_not_leak_absolute_path(tmp_path, monkeypatch):
    feeds = tmp_path / "secret-parent" / "drive_cameras"
    feeds.mkdir(parents=True)
    monkeypatch.setenv("VIDEO_FEEDS_DIR", str(feeds))
    resp = _client(Role.analyst).get("/sources/mode")
    assert resp.status_code == 200
    assert str(tmp_path) not in resp.text
    assert "secret-parent" not in resp.text
    assert resp.json()["feeds_dir"] == "drive_cameras"


def _spawn(*marker: str) -> subprocess.Popen:
    # Own session so pid == pgid, like start_runner; argv carries the marker words.
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *marker],
        start_new_session=True,
    )


def _wait_for_exit(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_stop_runner_leaves_unrelated_process_with_stale_pid(tmp_path):
    other = _spawn("unrelated")
    try:
        (tmp_path / ".runner.pid").write_text(str(other.pid), encoding="utf-8")
        video_feeds.stop_runner(tmp_path)
        assert not (tmp_path / ".runner.pid").exists()
        assert not _wait_for_exit(other, timeout=0.5), "unrelated process was killed"
    finally:
        other.kill()
        other.wait()


def test_stop_runner_terminates_the_real_runner(tmp_path):
    runner = _spawn("-m", "ocr_engine.cli", "video-city")
    try:
        # Give the interpreter a moment so its command line is visible to ps or /proc.
        time.sleep(0.3)
        (tmp_path / ".runner.pid").write_text(str(runner.pid), encoding="utf-8")
        video_feeds.stop_runner(tmp_path)
        assert not (tmp_path / ".runner.pid").exists()
        assert _wait_for_exit(runner), "runner was not terminated"
    finally:
        if runner.poll() is None:
            runner.kill()
        runner.wait()
