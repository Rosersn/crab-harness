"""Memory repository — CRUD for per-user memory data."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import UserMemory


class MemoryRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def load(self, user_id: uuid.UUID, agent_name: str | None = None) -> dict | None:
        """Load memory data for a user+agent. Returns None if not found."""
        result = await self._db.execute(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.agent_name == agent_name,
            )
        )
        memory = result.scalar_one_or_none()
        if memory is None:
            return None
        return memory.memory_data  # type: ignore[return-value]

    async def save(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        memory_data: dict,
        agent_name: str | None = None,
    ) -> UserMemory:
        """Upsert memory data for a user+agent."""
        result = await self._db.execute(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.agent_name == agent_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.memory_data = memory_data  # type: ignore[assignment]
            existing.version = (existing.version or 0) + 1
            await self._db.flush()
            return existing
        memory = UserMemory(
            user_id=user_id,
            tenant_id=tenant_id,
            agent_name=agent_name,
            memory_data=memory_data,
        )
        self._db.add(memory)
        await self._db.flush()
        return memory

    async def get_all_for_user(self, user_id: uuid.UUID) -> list[UserMemory]:
        result = await self._db.execute(
            select(UserMemory).where(UserMemory.user_id == user_id)
        )
        return list(result.scalars().all())

    async def delete(self, user_id: uuid.UUID, agent_name: str | None = None) -> bool:
        result = await self._db.execute(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.agent_name == agent_name,
            )
        )
        memory = result.scalar_one_or_none()
        if memory is None:
            return False
        await self._db.delete(memory)
        await self._db.flush()
        return True
