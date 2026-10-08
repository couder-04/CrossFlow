"""JWT authentication and password utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from anpr_common.config import Settings, get_settings
from anpr_common.passwords import hash_password, verify_password
from jose import JWTError, jwt
from pydantic import BaseModel

ALGORITHM = "HS256"


class Role(str, Enum):
    admin = "admin"
    operator = "operator"
    analyst = "analyst"


class TokenPayload(BaseModel):
    sub: str
    role: Role
    exp: datetime | None = None


class UserContext(BaseModel):
    id: str
    username: str
    role: Role


__all__ = [
    "ALGORITHM",
    "Role",
    "TokenPayload",
    "UserContext",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]


def create_access_token(
    username: str,
    role: Role,
    settings: Settings | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    settings = settings or get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": username,
        "role": role.value,
        "exp": expire,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> UserContext:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role_str = payload.get("role")
        user_id = payload.get("uid", "")
        if not username or not role_str:
            raise JWTError("Missing claims")
        return UserContext(id=str(user_id), username=username, role=Role(role_str))
    except (JWTError, ValueError) as exc:
        raise JWTError("Invalid token") from exc
