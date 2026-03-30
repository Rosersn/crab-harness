"""Upload metadata repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.models import UploadMetadata


class UploadRepo:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        thread_id: uuid.UUID,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        filename: str,
        size_bytes: int,
        bos_key: str,
        markdown_bos_key: str | None = None,
    ) -> UploadMetadata:
        upload = UploadMetadata(
            thread_id=thread_id,
            user_id=user_id,
            tenant_id=tenant_id,
            filename=filename,
            size_bytes=size_bytes,
            bos_key=bos_key,
            markdown_bos_key=markdown_bos_key,
        )
        self._db.add(upload)
        await self._db.flush()
        return upload

    async def list_for_thread(self, thread_id: uuid.UUID, user_id: uuid.UUID) -> list[UploadMetadata]:
        result = await self._db.execute(
            select(UploadMetadata)
            .where(UploadMetadata.thread_id == thread_id, UploadMetadata.user_id == user_id)
            .order_by(UploadMetadata.created_at)
        )
        return list(result.scalars().all())

    async def list_for_user(self, user_id: uuid.UUID) -> list[UploadMetadata]:
        result = await self._db.execute(
            select(UploadMetadata)
            .where(UploadMetadata.user_id == user_id)
            .order_by(UploadMetadata.created_at)
        )
        return list(result.scalars().all())

    async def get_by_id(self, upload_id: uuid.UUID) -> UploadMetadata | None:
        result = await self._db.execute(
            select(UploadMetadata).where(UploadMetadata.id == upload_id)
        )
        return result.scalar_one_or_none()

    async def get_by_filename(
        self, thread_id: uuid.UUID, user_id: uuid.UUID, filename: str
    ) -> UploadMetadata | None:
        result = await self._db.execute(
            select(UploadMetadata).where(
                UploadMetadata.thread_id == thread_id,
                UploadMetadata.user_id == user_id,
                UploadMetadata.filename == filename,
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, upload_id: uuid.UUID) -> bool:
        upload = await self.get_by_id(upload_id)
        if upload is None:
            return False
        await self._db.delete(upload)
        await self._db.flush()
        return True

    async def count_for_thread(self, thread_id: uuid.UUID) -> int:
        result = await self._db.execute(
            select(func.count()).select_from(UploadMetadata).where(
                UploadMetadata.thread_id == thread_id
            )
        )
        return result.scalar_one()
