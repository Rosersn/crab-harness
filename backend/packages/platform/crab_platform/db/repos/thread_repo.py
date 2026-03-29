"""Thread and Run repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import Run, Thread


class ThreadRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs) -> Thread:
        thread = Thread(tenant_id=tenant_id, user_id=user_id, **kwargs)
        self._db.add(thread)
        await self._db.flush()
        return thread

    async def get(self, thread_id: uuid.UUID) -> Thread | None:
        result = await self._db.execute(select(Thread).where(Thread.id == thread_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50, offset: int = 0) -> list[Thread]:
        result = await self._db.execute(
            select(Thread)
            .where(Thread.tenant_id == tenant_id, Thread.user_id == user_id, Thread.is_archived.is_(False))
            .order_by(Thread.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def delete(self, thread_id: uuid.UUID) -> bool:
        thread = await self.get(thread_id)
        if thread is None:
            return False
        await self._db.delete(thread)
        await self._db.flush()
        return True


class RunRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, thread_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs) -> Run:
        run = Run(thread_id=thread_id, tenant_id=tenant_id, user_id=user_id, **kwargs)
        self._db.add(run)
        await self._db.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> Run | None:
        result = await self._db.execute(select(Run).where(Run.id == run_id))
        return result.scalar_one_or_none()

    async def get_active_run(self, thread_id: uuid.UUID) -> Run | None:
        result = await self._db.execute(
            select(Run).where(Run.thread_id == thread_id, Run.status == "running")
        )
        return result.scalar_one_or_none()

    async def update_status(self, run_id: uuid.UUID, status: str, **kwargs) -> None:
        values: dict = {"status": status}
        if status == "running":
            values["started_at"] = datetime.now(UTC)
        if status in ("succeeded", "failed", "cancelled"):
            values["finished_at"] = datetime.now(UTC)
        if "error" in kwargs:
            values["error"] = kwargs["error"]
        if "gateway_instance_id" in kwargs:
            values["gateway_instance_id"] = kwargs["gateway_instance_id"]
        await self._db.execute(update(Run).where(Run.id == run_id).values(**values))
        await self._db.flush()

    async def mark_orphaned_runs_failed(self, gateway_instance_id: str) -> int:
        """Mark all running runs for this instance as failed (crash recovery)."""
        result = await self._db.execute(
            update(Run)
            .where(Run.status == "running", Run.gateway_instance_id == gateway_instance_id)
            .values(status="failed", finished_at=datetime.now(UTC), error="Gateway instance restarted")
        )
        await self._db.flush()
        return result.rowcount
