"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

import clickhouse_connect
import redis.asyncio as aioredis
from anpr_common.config import Settings, get_settings
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from minio import Minio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import Role, UserContext, decode_access_token
from api.db import User, get_session

security = HTTPBearer(auto_error=False)

_clickhouse_client = None
_redis_client: aioredis.Redis | None = None
_minio_client: Minio | None = None


def get_settings_dep() -> Settings:
    return get_settings()


def get_clickhouse(settings: Settings = Depends(get_settings_dep)):
    global _clickhouse_client
    if _clickhouse_client is None:
        _clickhouse_client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password or "",
            database=settings.clickhouse_db,
        )
    return _clickhouse_client


async def get_redis(settings: Settings = Depends(get_settings_dep)) -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


_minio_presign_client: Minio | None = None


def get_minio(settings: Settings = Depends(get_settings_dep)) -> Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _minio_client


def get_minio_presign(settings: Settings = Depends(get_settings_dep)) -> Minio:
    """Client used only to sign URLs for the browser (MINIO_PUBLIC_ENDPOINT).

    Inside Docker MINIO_ENDPOINT is ``minio:9000``, which browsers can't resolve, and the host is
    part of the signature, so presigned URLs must be signed for the public host. The region is
    fixed so signing never makes a network call to that host (it may not be reachable from here).
    """
    global _minio_presign_client
    if _minio_presign_client is None:
        _minio_presign_client = Minio(
            settings.minio_public_endpoint or settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
    return _minio_presign_client


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user = decode_access_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    # The token only identifies the account. Look the row up on every request so deleted users
    # are rejected and role changes apply immediately instead of living on in a stale token.
    stmt = select(User)
    if user.id:
        try:
            stmt = stmt.where(User.id == UUID(user.id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            ) from exc
    else:
        stmt = stmt.where(User.username == user.username)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    try:
        role = Role(row.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        ) from exc
    return UserContext(id=str(row.id), username=row.username, role=role)


def require_roles(*allowed: Role) -> Callable:
    allowed_set = set(allowed)

    async def _checker(user: Annotated[UserContext, Depends(get_current_user)]) -> UserContext:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' not permitted for this endpoint",
            )
        return user

    return _checker


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[UserContext, Depends(get_current_user)]
ClickHouseDep = Annotated[Any, Depends(get_clickhouse)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
MinioDep = Annotated[Minio, Depends(get_minio)]
MinioPresignDep = Annotated[Minio, Depends(get_minio_presign)]

AdminUserDep = Annotated[UserContext, Depends(require_roles(Role.admin))]
OperatorUserDep = Annotated[UserContext, Depends(require_roles(Role.admin, Role.operator))]
