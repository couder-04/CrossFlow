"""Camera wall: live-frame flag and presigned JPEG URL."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.auth import Role, create_access_token
from api.deps import get_minio, get_minio_presign, get_session
from api.main import create_app
from api.routes.cameras import frame_object_key
from fastapi.testclient import TestClient


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _Rows(self._rows)


class _Minio:
    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.presigns: list[str] = []

    def stat_object(self, _bucket: str, key: str):
        if key not in self.present:
            raise FileNotFoundError(key)
        return SimpleNamespace(object_name=key)

    def presigned_get_object(self, _bucket: str, key: str, expires=None):
        self.presigns.append(key)
        return f"http://minio.local/{key}?sig=1"


def _camera(camera_id: str, name: str):
    return SimpleNamespace(
        id=camera_id,
        name=name,
        heading_deg=15.0,
        lanes=2,
        allowed_direction="N",
        osm_u=None,
        osm_v=None,
        status="active",
    )


@pytest.fixture
def wall_client():
    present = {frame_object_key("cam-live")}
    minio = _Minio(present)
    rows = [
        (_camera("cam-live", "Gate A"), 18.52, 73.85),
        (_camera("cam-dark", "Gate B"), 18.53, 73.86),
        (_camera("vid-01", "Traffic clip"), 18.52, 73.86),
    ]

    async def _session():
        yield _Session(rows)

    with (
        patch("api.main.ensure_seed_users", new=AsyncMock()),
        patch("api.main.apply_postgres_upgrade", new=AsyncMock()),
    ):
        app = create_app()
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_minio] = lambda: minio
        app.dependency_overrides[get_minio_presign] = lambda: minio  # signs the browser URLs
        token = create_access_token("admin", Role.admin, extra={"uid": "admin-id"})
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, minio


def test_camera_list_has_live_frame(wall_client):
    client, _minio = wall_client
    resp = client.get("/cameras")
    assert resp.status_code == 200
    by_id = {row["id"]: row for row in resp.json()}
    assert by_id["cam-live"]["has_live_frame"] is True
    assert by_id["cam-dark"]["has_live_frame"] is False
    assert by_id["cam-live"]["name"] == "Gate A"
    assert by_id["cam-live"]["lat"] == pytest.approx(18.52)
    assert by_id["cam-live"]["lng"] == pytest.approx(73.85)
    assert "vid-01" not in by_id


def test_video_source_lists_only_video_cameras(wall_client):
    client, _minio = wall_client
    resp = client.get("/cameras?source=video")
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert ids == ["vid-01"]


def test_camera_frame_url_or_404(wall_client):
    client, minio = wall_client
    missing = client.get("/cameras/cam-dark/frame")
    assert missing.status_code == 404

    found = client.get("/cameras/cam-live/frame")
    assert found.status_code == 200
    body = found.json()
    assert isinstance(body["url"], str)
    assert body["url"].startswith("http://minio.local/")
    assert frame_object_key("cam-live") in minio.presigns
