"""Media uploads are streamed to disk instead of held as one bytes object."""

from __future__ import annotations

import resource
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from api.auth import Role, UserContext
from api.db import Camera
from api.deps import get_current_user, get_minio, get_session
from api.main import create_app
from fastapi.testclient import TestClient


def _rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return rss
    return rss * 1024


class _Session:
    def __init__(self) -> None:
        self.added: list = []

    async def get(self, model, key):
        if model is Camera:
            return SimpleNamespace(id=key)
        return None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _row) -> None:
        return None


class _Minio:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, bucket, key, data, length, content_type=None):
        self.calls.append((type(data).__name__, length, content_type, key))
        data.read(1024)


def test_video_upload_streams_without_large_rss_growth(tmp_path):
    header = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
    blob_path = tmp_path / "clip.mp4"
    size = 20 * 1024 * 1024
    with blob_path.open("wb") as handle:
        handle.write(header)
        handle.write(b"\x00" * (size - len(header)))

    session = _Session()
    minio = _Minio()

    async def _session():
        yield session

    with (
        patch("api.main.ensure_seed_users", new=AsyncMock()),
        patch("api.main.apply_postgres_upgrade", new=AsyncMock()),
        patch("api.main.apply_stale_job_recovery", new=AsyncMock()),
        patch("api.routes.uploads._process_media", new=AsyncMock()),
    ):
        app = create_app()
        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_minio] = lambda: minio
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            id="11111111-1111-1111-1111-111111111111",
            username="operator",
            role=Role.operator,
        )
        client = TestClient(app)
        before = _rss_bytes()
        with blob_path.open("rb") as handle:
            resp = client.post(
                "/uploads/media",
                data={"kind": "video", "camera_id": "cam-1"},
                files={"file": ("clip.mp4", handle, "video/mp4")},
            )
        after = _rss_bytes()

    assert resp.status_code == 200, resp.text
    assert after - before < 100 * 1024 * 1024
    assert minio.calls
    assert minio.calls[0][0] != "BytesIO"
    assert minio.calls[0][1] == size
