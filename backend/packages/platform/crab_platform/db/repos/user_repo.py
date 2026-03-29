"""User repository - CRUD for users and tenants."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.auth.password import hash_password
from crab_platform.db.models import Tenant, User, UserQuota


class UserRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_tenant(self, name: str, slug: str) -> Tenant:
        tenant = Tenant(name=name, slug=slug)
        self._db.add(tenant)
        await self._db.flush()
        return tenant

    async def create_user(self, tenant_id: uuid.UUID, email: str, password: str, role: str = "member") -> User:
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            role=role,
        )
        self._db.add(user)
        await self._db.flush()
        # Create default quota
        quota = UserQuota(user_id=user.id, tenant_id=tenant_id)
        self._db.add(quota)
        await self._db.flush()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
