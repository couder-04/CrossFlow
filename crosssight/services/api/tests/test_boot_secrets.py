"""Boot refuses default secrets outside dev."""

from __future__ import annotations

import asyncio
import logging

import pytest
from anpr_common.config import DEFAULT_JWT_SECRET, Settings
from api.db import User
from api.main import _validate_settings
from api.seed import ensure_seed_users

_STRONG = {
    "jwt_secret": "x" * 40,
    "postgres_password": "not-anpr",
    "minio_secret_key": "not-minioadmin",
}
_SEEDS = {
    "seed_admin_pass": "a-strong-admin-pass",
    "seed_operator_pass": "a-strong-operator-pass",
    "seed_analyst_pass": "a-strong-analyst-pass",
}


def test_prod_rejects_default_jwt_secret():
    settings = Settings(app_env="prod", jwt_secret=DEFAULT_JWT_SECRET)
    with pytest.raises(RuntimeError):
        _validate_settings(settings)


def test_dev_allows_default_jwt_secret():
    settings = Settings(app_env="dev", jwt_secret=DEFAULT_JWT_SECRET)
    _validate_settings(settings)


@pytest.mark.parametrize("env", ["staging", "prod"])
@pytest.mark.parametrize(
    "field,name,default",
    [
        ("seed_admin_pass", "SEED_ADMIN_PASS", "admin123"),
        ("seed_operator_pass", "SEED_OPERATOR_PASS", "operator123"),
        ("seed_analyst_pass", "SEED_ANALYST_PASS", "analyst123"),
    ],
)
def test_non_dev_rejects_default_seed_password(env, field, name, default):
    rotated = Settings(app_env=env, **{**_STRONG, **_SEEDS})  # type: ignore[arg-type]
    _validate_settings(rotated)  # everything rotated: boots
    settings = Settings(app_env=env, **{**_STRONG, **_SEEDS, field: default})  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match=name):
        _validate_settings(settings)


def test_dev_warns_and_allows_default_seed_passwords(caplog):
    settings = Settings(app_env="dev", jwt_secret=DEFAULT_JWT_SECRET)
    with caplog.at_level(logging.WARNING, logger="api.main"):
        _validate_settings(settings)
    assert any("APP_ENV=dev" in r.getMessage() for r in caplog.records)


class _Result:
    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Session:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}

    def add(self, user: User) -> None:
        self.users[user.username] = user

    async def execute(self, statement):
        params = statement.compile().params
        username = next(value for value in params.values() if isinstance(value, str))
        return _Result(self.users.get(username))

    async def commit(self) -> None:
        return None


def test_non_dev_skips_seed_users_with_default_password():
    session = _Session()
    settings = Settings(  # type: ignore[arg-type]
        app_env="prod", **{**_STRONG, **_SEEDS, "seed_operator_pass": "operator123"}
    )
    asyncio.run(ensure_seed_users(session, settings))
    assert set(session.users) == {"admin", "analyst"}


def test_dev_still_seeds_default_password_users():
    session = _Session()
    asyncio.run(ensure_seed_users(session, Settings(app_env="dev")))
    assert set(session.users) == {"admin", "operator", "analyst"}
