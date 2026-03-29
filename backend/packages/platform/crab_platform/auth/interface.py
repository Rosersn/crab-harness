"""Auth provider interface and user data classes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AuthenticatedUser:
    """Immutable user identity extracted from a verified token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str  # "admin" | "member"


@runtime_checkable
class AuthProvider(Protocol):
    """Pluggable authentication provider."""

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        """Verify an access token and return the user, or None if invalid."""
        ...

    async def refresh(self, refresh_token: str) -> tuple[str, str] | None:
        """Exchange a refresh token for new (access_token, refresh_token), or None if invalid."""
        ...
