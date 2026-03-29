"""MCP configuration repository — per-user MCP server configs."""

from __future__ import annotations

import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import UserMcpConfig


class McpConfigRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserMcpConfig]:
        result = await self._db.execute(
            select(UserMcpConfig)
            .where(UserMcpConfig.user_id == user_id)
            .order_by(UserMcpConfig.created_at)
        )
        return list(result.scalars().all())

    async def get_by_name(self, user_id: uuid.UUID, server_name: str) -> UserMcpConfig | None:
        result = await self._db.execute(
            select(UserMcpConfig).where(
                UserMcpConfig.user_id == user_id,
                UserMcpConfig.server_name == server_name,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        server_name: str,
        enabled: bool,
        transport_type: str,
        config: dict,
    ) -> UserMcpConfig:
        """Create or update an MCP server config for a user."""
        # Enforce: only http/sse allowed (no stdio)
        if transport_type not in ("http", "sse"):
            raise ValueError(f"Only 'http' and 'sse' transports are allowed, got: {transport_type}")

        existing = await self.get_by_name(user_id, server_name)
        if existing is not None:
            existing.enabled = enabled
            existing.transport_type = transport_type
            existing.config = config  # type: ignore[assignment]
            await self._db.flush()
            return existing

        mcp = UserMcpConfig(
            user_id=user_id,
            tenant_id=tenant_id,
            server_name=server_name,
            enabled=enabled,
            transport_type=transport_type,
            config=config,
        )
        self._db.add(mcp)
        await self._db.flush()
        return mcp

    async def delete(self, user_id: uuid.UUID, server_name: str) -> bool:
        existing = await self.get_by_name(user_id, server_name)
        if existing is None:
            return False
        await self._db.delete(existing)
        await self._db.flush()
        return True

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._db.execute(
            select(func.count()).select_from(UserMcpConfig).where(
                UserMcpConfig.user_id == user_id
            )
        )
        return result.scalar_one()
