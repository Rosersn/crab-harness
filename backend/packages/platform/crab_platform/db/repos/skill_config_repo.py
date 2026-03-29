"""Skill configuration repository — per-user skill enable/disable + custom skills."""

from __future__ import annotations

import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import UserSkillConfig


class SkillConfigRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserSkillConfig]:
        result = await self._db.execute(
            select(UserSkillConfig)
            .where(UserSkillConfig.user_id == user_id)
            .order_by(UserSkillConfig.created_at)
        )
        return list(result.scalars().all())

    async def get_by_name(self, user_id: uuid.UUID, skill_name: str) -> UserSkillConfig | None:
        result = await self._db.execute(
            select(UserSkillConfig).where(
                UserSkillConfig.user_id == user_id,
                UserSkillConfig.skill_name == skill_name,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        skill_name: str,
        enabled: bool,
        bos_key: str | None = None,
    ) -> UserSkillConfig:
        """Create or update a skill config for a user."""
        existing = await self.get_by_name(user_id, skill_name)
        if existing is not None:
            existing.enabled = enabled
            if bos_key is not None:
                existing.bos_key = bos_key
            await self._db.flush()
            return existing

        skill = UserSkillConfig(
            user_id=user_id,
            tenant_id=tenant_id,
            skill_name=skill_name,
            enabled=enabled,
            bos_key=bos_key,
        )
        self._db.add(skill)
        await self._db.flush()
        return skill

    async def delete(self, user_id: uuid.UUID, skill_name: str) -> bool:
        existing = await self.get_by_name(user_id, skill_name)
        if existing is None:
            return False
        await self._db.delete(existing)
        await self._db.flush()
        return True

    async def count_custom_for_user(self, user_id: uuid.UUID) -> int:
        """Count user's custom skills (those with a bos_key)."""
        result = await self._db.execute(
            select(func.count()).select_from(UserSkillConfig).where(
                UserSkillConfig.user_id == user_id,
                UserSkillConfig.bos_key.isnot(None),
            )
        )
        return result.scalar_one()
