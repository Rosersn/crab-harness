"""JWT-based AuthProvider implementation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.auth.password import verify_password
from crab_platform.config.platform_config import get_platform_config
from crab_platform.db.models import User


class JWTAuthProvider:
    """Built-in JWT auth provider (HS256)."""

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session
        self._config = get_platform_config()

    # -- AuthProvider protocol ------------------------------------------------

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        """Decode and verify an access token."""
        try:
            payload = jwt.decode(token, self._config.jwt_secret, algorithms=[self._config.jwt_algorithm])
        except jwt.PyJWTError:
            return None

        if payload.get("type") != "access":
            return None

        try:
            return AuthenticatedUser(
                user_id=uuid.UUID(payload["sub"]),
                tenant_id=uuid.UUID(payload["tid"]),
                email=payload["email"],
                role=payload.get("role", "member"),
            )
        except (KeyError, ValueError):
            return None

    async def refresh(self, refresh_token: str) -> tuple[str, str] | None:
        """Exchange a refresh token for a new token pair."""
        try:
            payload = jwt.decode(refresh_token, self._config.jwt_secret, algorithms=[self._config.jwt_algorithm])
        except jwt.PyJWTError:
            return None

        if payload.get("type") != "refresh":
            return None

        try:
            user_id = uuid.UUID(payload["sub"])
        except (KeyError, ValueError):
            return None

        # Verify user still exists
        result = await self._db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            return None

        access = self._create_access_token(user)
        new_refresh = self._create_refresh_token(user)
        return access, new_refresh

    # -- Login (not part of AuthProvider protocol) ----------------------------

    async def login(self, email: str, password: str) -> tuple[str, str] | None:
        """Authenticate by email/password. Returns (access_token, refresh_token) or None."""
        result = await self._db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            return None

        access = self._create_access_token(user)
        refresh = self._create_refresh_token(user)
        return access, refresh

    # -- Token creation -------------------------------------------------------

    def _create_access_token(self, user: User) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user.id),
            "tid": str(user.tenant_id),
            "email": user.email,
            "role": user.role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=self._config.jwt_access_token_expire_minutes),
        }
        return jwt.encode(payload, self._config.jwt_secret, algorithm=self._config.jwt_algorithm)

    def _create_refresh_token(self, user: User) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user.id),
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(days=self._config.jwt_refresh_token_expire_days),
        }
        return jwt.encode(payload, self._config.jwt_secret, algorithm=self._config.jwt_algorithm)
