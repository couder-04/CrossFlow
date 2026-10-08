"""Tokens are checked against the users table on every request; logins are throttled."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.auth import Role, create_access_token
from api.db import get_session
from api.deps import get_current_user, get_redis
from api.main import create_app
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _fast_password_check(monkeypatch):
    monkeypatch.setattr("api.routes.auth.verify_password", lambda plain, stored: plain == stored)


class FakeSession:
    """Resolves ``User.id == uuid`` / ``User.username == str`` lookups against a dict."""

    def __init__(self) -> None:
        self.users: dict[uuid.UUID, SimpleNamespace] = {}

    def add_user(self, username: str, role: str, password: str = "pw") -> SimpleNamespace:
        user = SimpleNamespace(
            id=uuid.uuid4(),
            username=username,
            role=role,
            password_hash=password,  # compared verbatim by the patched verify_password below
        )
        self.users[user.id] = user
        return user

    async def execute(self, stmt):
        wanted = next(iter(stmt.compile().params.values()))
        if isinstance(wanted, uuid.UUID):
            row = self.users.get(wanted)
        else:
            row = next((u for u in self.users.values() if u.username == wanted), None)
        return SimpleNamespace(scalar_one_or_none=lambda: row)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def delete(self, key):
        self.data.pop(key, None)


class BrokenRedis:
    async def get(self, key):
        raise ConnectionError("redis down")

    incr = expire = delete = get


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_deleted_user_token_is_rejected():
    session = FakeSession()
    user = session.add_user("alice", "operator")
    token = create_access_token("alice", Role.operator, extra={"uid": str(user.id)})
    assert (await get_current_user(_bearer(token), session)).username == "alice"  # type: ignore[arg-type]

    del session.users[user.id]
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_bearer(token), session)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


async def test_role_comes_from_the_database_not_the_token():
    session = FakeSession()
    user = session.add_user("bob", "admin")
    token = create_access_token("bob", Role.admin, extra={"uid": str(user.id)})
    assert (await get_current_user(_bearer(token), session)).role == Role.admin  # type: ignore[arg-type]

    user.role = "analyst"  # demoted after the token was issued
    ctx = await get_current_user(_bearer(token), session)  # type: ignore[arg-type]
    assert ctx.role == Role.analyst
    assert ctx.id == str(user.id)


async def test_token_without_uid_falls_back_to_username():
    session = FakeSession()
    session.add_user("carol", "analyst")
    token = create_access_token("carol", Role.admin)  # no uid, forged-looking role
    ctx = await get_current_user(_bearer(token), session)  # type: ignore[arg-type]
    assert ctx.role == Role.analyst

    ghost = create_access_token("nobody", Role.admin)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_bearer(ghost), session)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


async def test_malformed_uid_is_401():
    token = create_access_token("x", Role.admin, extra={"uid": "not-a-uuid"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_bearer(token), FakeSession())  # type: ignore[arg-type]
    assert exc.value.status_code == 401


def _login_client(session: FakeSession, redis) -> TestClient:
    with patch("api.main.ensure_seed_users", new=AsyncMock()):
        app = create_app()

    async def _session():
        yield session

    async def _redis():
        return redis

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient, password: str = "wrong", username: str = "dave"):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_eleventh_failed_login_is_throttled():
    session, redis = FakeSession(), FakeRedis()
    session.add_user("dave", "analyst", password="right")
    client = _login_client(session, redis)

    assert [_login(client).status_code for _ in range(10)] == [401] * 10
    resp = _login(client)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # Locked out even with the right password until the window expires.
    assert _login(client, "right").status_code == 429
    assert list(redis.ttls.values()) == [15 * 60]


def test_throttle_is_per_username():
    session, redis = FakeSession(), FakeRedis()
    session.add_user("dave", "analyst", password="right")
    session.add_user("erin", "analyst", password="right")
    client = _login_client(session, redis)
    for _ in range(11):
        _login(client)
    assert _login(client, "right", "erin").status_code == 200


def test_successful_login_resets_counter():
    session, redis = FakeSession(), FakeRedis()
    session.add_user("dave", "analyst", password="right")
    client = _login_client(session, redis)
    for _ in range(9):
        assert _login(client).status_code == 401
    assert _login(client, "right").status_code == 200
    assert redis.data == {}
    assert [_login(client).status_code for _ in range(10)] == [401] * 10


def test_redis_outage_degrades_open(caplog):
    session = FakeSession()
    session.add_user("dave", "analyst", password="right")
    client = _login_client(session, BrokenRedis())
    assert [_login(client).status_code for _ in range(12)] == [401] * 12
    assert _login(client, "right").status_code == 200
    assert any("throttling unavailable" in r.getMessage() for r in caplog.records)
