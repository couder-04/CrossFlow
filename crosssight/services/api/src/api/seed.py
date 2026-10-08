"""User seeding helpers."""

from __future__ import annotations

import logging
from uuid import uuid4

from anpr_common.config import DEFAULT_SEED_PASSWORDS, Settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import Role, hash_password
from api.db import User

logger = logging.getLogger(__name__)


async def ensure_seed_users(session: AsyncSession, settings: Settings) -> None:
    seeds = (
        (settings.seed_admin_user, settings.seed_admin_pass, Role.admin),
        (settings.seed_operator_user, settings.seed_operator_pass, Role.operator),
        (settings.seed_analyst_user, settings.seed_analyst_pass, Role.analyst),
    )
    for username, password, role in seeds:
        result = await session.execute(select(User).where(User.username == username))
        existing = result.scalar_one_or_none()
        if existing is None:
            if settings.app_env != "dev" and password in DEFAULT_SEED_PASSWORDS:
                logger.warning(
                    "Not creating seed user %s: its password is the demo default and APP_ENV=%s",
                    username,
                    settings.app_env,
                )
                continue
            session.add(
                User(
                    id=uuid4(),
                    username=username,
                    password_hash=hash_password(password),
                    role=role.value,
                )
            )
        elif settings.app_env == "dev":
            existing.password_hash = hash_password(password)
            existing.role = role.value
        else:
            logger.warning(
                "Seed user %s already exists; leaving password and role unchanged while APP_ENV=%s",
                username,
                settings.app_env,
            )
    await session.commit()
