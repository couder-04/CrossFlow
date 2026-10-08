"""/crops must only sign crop keys, and /exports must not list plate exports to analysts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from anpr_common.intelligence.access import PLATE_EXPORTS
from api import deps
from api.auth import Role, UserContext
from api.db import get_session
from api.main import create_app
from fastapi.testclient import TestClient


class FakeMinio:
    def __init__(self) -> None:
        self.presigned: list[str] = []

    def presigned_get_object(self, bucket, key, expires=None):
        self.presigned.append(key)
        return f"http://minio/{bucket}/{key}?sig=1"


def _row(kind: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        format="csv",
        status="ready",
        requested_by="admin",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        filters={},
        filename=f"{kind}.csv",
        error=None,
        expires_at=None,
        object_key=f"exports/x/{kind}.csv",
    )


class FakeSession:
    """Applies the NOT IN (kinds) filter of the compiled statement to in-memory rows."""

    def __init__(self, rows) -> None:
        self.rows = rows

    async def execute(self, stmt):
        compiled = stmt.compile()
        excluded: set[str] = set()
        for value in compiled.params.values():
            if isinstance(value, (list, tuple, set, frozenset)):
                excluded |= set(value)
        keep = [r for r in self.rows if r.kind not in excluded]
        return SimpleNamespace(scalars=lambda: iter(keep))

    async def get(self, _model, export_id):
        return next((r for r in self.rows if r.id == export_id), None)


@pytest.fixture
def make_client():
    def make(role: Role, rows=()):
        with patch("api.main.ensure_seed_users", new=AsyncMock()):
            app = create_app()
        minio = FakeMinio()
        session = FakeSession(list(rows))

        async def _session():
            yield session

        app.dependency_overrides[deps.get_current_user] = lambda: UserContext(
            id="1", username=role.value, role=role
        )
        app.dependency_overrides[deps.get_settings_dep] = lambda: SimpleNamespace(
            minio_bucket="anpr-crops"
        )
        app.dependency_overrides[deps.get_minio_presign] = lambda: minio
        app.dependency_overrides[get_session] = _session
        return TestClient(app, raise_server_exceptions=False), minio

    return make


@pytest.mark.parametrize(
    "key",
    [
        "exports/123/plates.csv",
        "exports/123/plates.jpg",
        "/exports/123/plates.csv",
        "uploads/video/1/clip.mp4",
        "imports/7/watchlist.csv",
        "frames/latest/cam-1.jpg",
        "evidence/a/b.jpg",
        "exports/plates.jpg",
        "cam-1/abc.mp4",
        "cam-1/sub/abc.jpg",
    ],
)
def test_analyst_cannot_presign_non_crop_keys(make_client, key):
    client, minio = make_client(Role.analyst)
    resp = client.get("/crops", params={"key": key})
    assert resp.status_code == 403, key
    assert minio.presigned == []


@pytest.mark.parametrize("key", ["cam-1\\x.jpg", "cam-1/..\\x.jpg", "a/../exports/x.jpg", "\\x"])
def test_malformed_keys_rejected(make_client, key):
    client, minio = make_client(Role.analyst)
    assert client.get("/crops", params={"key": key}).status_code == 400
    assert minio.presigned == []


def test_analyst_gets_valid_crop(make_client):
    client, minio = make_client(Role.analyst)
    resp = client.get("/crops", params={"key": "cam-1/6f1c.jpg"})
    assert resp.status_code == 200
    assert resp.json()["key"] == "cam-1/6f1c.jpg"
    assert minio.presigned == ["cam-1/6f1c.jpg"]


def test_analyst_export_list_excludes_plate_exports(make_client):
    kinds = ["traffic", "od", "incidents", "registry", "watchlist", "flow", "sightings"]
    rows = [_row(k) for k in kinds]
    client, _ = make_client(Role.analyst, rows)
    got = {e["kind"] for e in client.get("/exports").json()}
    assert got == {"traffic", "od", "flow"}
    assert not got & PLATE_EXPORTS


@pytest.mark.parametrize("role", [Role.admin, Role.operator])
def test_privileged_roles_see_all_exports(make_client, role):
    rows = [_row(k) for k in ("traffic", "incidents", "registry")]
    client, _ = make_client(role, rows)
    assert {e["kind"] for e in client.get("/exports").json()} == {
        "traffic",
        "incidents",
        "registry",
    }


def test_analyst_cannot_fetch_plate_export_by_id(make_client):
    plate, agg = _row("incidents"), _row("traffic")
    client, _ = make_client(Role.analyst, [plate, agg])
    assert client.get(f"/exports/{plate.id}").status_code == 403
    assert client.get(f"/exports/{agg.id}").status_code == 200
