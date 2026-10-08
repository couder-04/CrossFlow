"""Apply idempotent Postgres upgrades on API startup."""

from __future__ import annotations

import logging

from anpr_common.intelligence.ddl import POSTGRES_STATEMENTS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def apply_postgres_upgrade(session: AsyncSession) -> None:
    for statement in POSTGRES_STATEMENTS:
        await session.execute(text(statement))
    await session.commit()
    logger.info("Postgres operational schema is current")
