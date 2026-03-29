"""BOS → E2B file injection.

Downloads uploaded files from object storage and writes them into an E2B
sandbox so the Agent can access them at ``/mnt/user-data/uploads/``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from crab_platform.sandbox.path_mapping import E2BPathMapping

if TYPE_CHECKING:
    from e2b import Sandbox as E2BSdkSandbox
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _sandbox_write(e2b_sandbox: E2BSdkSandbox, path: str, content: bytes) -> None:
    """Synchronous E2B files.write — intended to be called via ``asyncio.to_thread``."""
    e2b_sandbox.files.write(path, content)


async def inject_thread_uploads(
    session_factory: async_sessionmaker[AsyncSession],
    thread_id: str,
    e2b_sandbox: E2BSdkSandbox,
    path_mapping: E2BPathMapping | None = None,
) -> int:
    """Download uploaded files from BOS and write them into an E2B sandbox.

    This is called when a *new* sandbox is created for a thread (not when
    resuming a paused one, since paused sandboxes retain their filesystem).

    Sync E2B SDK calls (``files.write``) are offloaded to a thread via
    ``asyncio.to_thread`` so they don't block the event loop.

    Args:
        session_factory: SQLAlchemy async session factory.
        thread_id: Thread UUID as string.
        e2b_sandbox: Connected E2B SDK sandbox instance.

    Returns:
        Number of files injected.
    """
    from crab_platform.db.repos.thread_repo import ThreadRepo
    from crab_platform.db.repos.upload_repo import UploadRepo
    from crab_platform.storage import get_object_storage

    tid = uuid.UUID(thread_id)
    injected = 0
    path_mapping = path_mapping or E2BPathMapping()

    async with session_factory() as db:
        # Get the thread to find user_id
        thread = await ThreadRepo(db).get(tid)
        if thread is None:
            logger.debug("No thread found for %s, skipping file injection", thread_id)
            return 0

        uploads = await UploadRepo(db).list_for_thread(tid, thread.user_id)
        if not uploads:
            logger.debug("No uploads for thread %s, skipping injection", thread_id)
            return 0

        storage = get_object_storage()

        for upload in uploads:
            try:
                content = await storage.get(upload.bos_key)
                virtual_path = f"{path_mapping.virtual_user_data_root}/uploads/{upload.filename}"
                await asyncio.to_thread(
                    _sandbox_write,
                    e2b_sandbox,
                    path_mapping.map_path(virtual_path),
                    content,
                )
                injected += 1
                logger.debug("Injected %s into E2B sandbox", virtual_path)

                # Also inject markdown companion if it exists.
                # Use ``{filename}.extracted.md`` to avoid collisions with
                # user-uploaded .md files (e.g. ``report.pdf`` → ``report.pdf.extracted.md``).
                if upload.markdown_bos_key:
                    try:
                        md_content = await storage.get(upload.markdown_bos_key)
                        md_name = f"{upload.filename}.extracted.md"
                        md_path = f"{path_mapping.virtual_user_data_root}/uploads/{md_name}"
                        await asyncio.to_thread(
                            _sandbox_write,
                            e2b_sandbox,
                            path_mapping.map_path(md_path),
                            md_content,
                        )
                        injected += 1
                        logger.debug("Injected markdown %s into E2B sandbox", md_path)
                    except Exception:
                        logger.warning("Failed to inject markdown for %s", upload.filename, exc_info=True)

            except Exception:
                logger.warning("Failed to inject %s into E2B sandbox", upload.filename, exc_info=True)

    logger.info("Injected %d files into E2B sandbox for thread %s", injected, thread_id)
    return injected


async def inject_user_custom_skills(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    e2b_sandbox: E2BSdkSandbox,
    path_mapping: E2BPathMapping | None = None,
) -> int:
    """Download custom user skills from object storage into the sandbox."""
    from crab_platform.db.repos.skill_config_repo import SkillConfigRepo
    from crab_platform.storage import get_object_storage

    injected = 0
    storage = get_object_storage()
    path_mapping = path_mapping or E2BPathMapping()

    async with session_factory() as db:
        skill_configs = await SkillConfigRepo(db).list_for_user(user_id)

    for skill_config in skill_configs:
        if not skill_config.enabled or not skill_config.bos_key:
            continue

        prefix = skill_config.bos_key.rstrip("/") + "/"
        try:
            keys = await storage.list_keys(prefix)
        except Exception:
            logger.warning("Failed to list custom skill objects for %s", skill_config.skill_name, exc_info=True)
            continue

        for key in keys:
            relative = key.removeprefix(prefix).lstrip("/")
            if not relative:
                continue
            try:
                content = await storage.get(key)
                virtual_path = str(
                    PurePosixPath(path_mapping.virtual_skills_root)
                    / "custom"
                    / skill_config.skill_name
                    / relative
                )
                await asyncio.to_thread(
                    _sandbox_write,
                    e2b_sandbox,
                    path_mapping.map_path(virtual_path),
                    content,
                )
                injected += 1
                logger.debug("Injected custom skill file %s", virtual_path)
            except Exception:
                logger.warning("Failed to inject custom skill object %s", key, exc_info=True)

    return injected
