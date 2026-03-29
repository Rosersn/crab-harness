"""Message repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import Message


class MessageRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        thread_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: str,
        content,
        *,
        run_id: uuid.UUID | None = None,
        tool_calls=None,
        tool_call_id: str | None = None,
    ) -> Message:
        """Append a message to the thread. Automatically assigns the next sequence_num."""
        for _ in range(5):
            next_seq = await self._next_sequence_num(thread_id)
            msg = Message(
                thread_id=thread_id,
                run_id=run_id,
                tenant_id=tenant_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                sequence_num=next_seq,
            )
            try:
                async with self._db.begin_nested():
                    self._db.add(msg)
                    await self._db.flush()
                return msg
            except IntegrityError:
                continue

        raise RuntimeError(f"Failed to allocate sequence number for thread {thread_id}")

    async def list_for_thread(
        self, thread_id: uuid.UUID, *, limit: int = 200, offset: int = 0
    ) -> list[Message]:
        result = await self._db.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.sequence_num.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_latest(self, thread_id: uuid.UUID) -> Message | None:
        result = await self._db.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.sequence_num.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_for_thread(self, thread_id: uuid.UUID) -> int:
        result = await self._db.execute(
            select(func.count()).select_from(Message).where(Message.thread_id == thread_id)
        )
        return result.scalar_one()

    async def _next_sequence_num(self, thread_id: uuid.UUID) -> int:
        result = await self._db.execute(
            select(func.coalesce(func.max(Message.sequence_num), -1)).where(
                Message.thread_id == thread_id
            )
        )
        return result.scalar_one() + 1
