"""RBAC dependency and endpoint enforcement tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from api.auth import Role, UserContext, create_access_token
from api.deps import get_current_user, require_roles
from api.main import create_app
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        yield create_app()


def test_require_roles_allows_admin():
    checker = require_roles(Role.admin)

    async def _run(user: UserContext = Depends(checker)):
        return user

    app = FastAPI()

    @app.get("/admin-only")
    async def admin_only(user: UserContext = Depends(checker)):
        return {"role": user.role.value}

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        id="1", username="admin", role=Role.admin
    )
    resp = client.get("/admin-only")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_require_roles_blocks_analyst():
    checker = require_roles(Role.admin, Role.operator)
    app = FastAPI()

    @app.get("/operator")
    async def operator_only(user: UserContext = Depends(checker)):
        return {"ok": True}

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        id="2", username="analyst", role=Role.analyst
    )
    resp = client.get("/operator")
    assert resp.status_code == 403
    assert "not permitted" in resp.json()["detail"]


def test_trajectory_returns_403_for_analyst_token(app):
    token = create_access_token("analyst", Role.analyst, extra={"uid": "analyst-id"})
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        "/trajectory",
        params={"plate": "BR01AB1234", "case_id": "CASE-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_trajectory_requires_authentication(app):
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/trajectory",
        params={"plate": "BR01AB1234", "case_id": "CASE-001"},
    )
    assert resp.status_code == 401


def test_healthz_is_public(app):
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
