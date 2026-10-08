"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from api.auth import Role, create_access_token, verify_password
from api.db import User
from api.deps import SessionDep, SettingsDep
from api.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> LoginResponse:
    result = await session.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    role = Role(user.role)
    token = create_access_token(
        user.username,
        role,
        settings,
        extra={"uid": str(user.id)},
    )
    return LoginResponse(access_token=token, role=role, username=user.username)
