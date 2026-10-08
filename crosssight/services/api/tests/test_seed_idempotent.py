"""Production boots must not reset a rotated seed password."""

from __future__ import annotations

import asyncio

from anpr_common.config import Settings
from api.db import User
from api.seed import ensure_seed_users


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


def test_prod_seed_keeps_rotated_password():
    session = _Session()
    settings = Settings(  # type: ignore[arg-type]
        app_env="prod",
        jwt_secret="not-the-default-secret-value-here",
        seed_admin_pass="rotated-admin-pass",
        seed_operator_pass="rotated-operator-pass",
        seed_analyst_pass="rotated-analyst-pass",
    )

    async def _run() -> None:
        await ensure_seed_users(session, settings)
        admin = session.users["admin"]
        admin.password_hash = "rotated-hash"
        await ensure_seed_users(session, settings)

    asyncio.run(_run())
    assert session.users["admin"].password_hash == "rotated-hash"
