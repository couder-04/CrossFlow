"""Authentication routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select

from api.auth import Role, create_access_token, verify_password
from api.db import User
from api.deps import RedisDep, SessionDep, SettingsDep
from api.schemas import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60


def _throttle_key(username: str, request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"login_fail:{username[:128]}:{ip}"


async def _failures(redis: Redis, key: str) -> int:
    try:
        return int(await redis.get(key) or 0)
    except Exception:
        logger.warning("Login throttling unavailable (Redis); allowing attempt", exc_info=True)
        return 0


async def _record_failure(redis: Redis, key: str) -> None:
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, LOGIN_WINDOW_SECONDS)
    except Exception:
        logger.warning("Login throttling unavailable (Redis); failure not counted", exc_info=True)


async def _clear_failures(redis: Redis, key: str) -> None:
    try:
        await redis.delete(key)
    except Exception:
        logger.warning("Login throttling unavailable (Redis); counter not cleared", exc_info=True)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
) -> LoginResponse:
    key = _throttle_key(body.username, request)
    if await _failures(redis, key) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed logins; try again later",
            headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)},
        )
    result = await session.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        await _record_failure(redis, key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    await _clear_failures(redis, key)
    role = Role(user.role)
    token = create_access_token(
        user.username,
        role,
        settings,
        extra={"uid": str(user.id)},
    )
    return LoginResponse(access_token=token, role=role, username=user.username)
