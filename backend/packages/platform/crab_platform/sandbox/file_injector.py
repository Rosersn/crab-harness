"""BOS → E2B file injection.

Downloads uploaded files from object storage and writes them into an E2B
sandbox so the Agent can access them at ``/mnt/user-data/uploads/``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shlex
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from crab_platform.sandbox.path_mapping import E2BPathMapping

if TYPE_CHECKING:
    from e2b import Sandbox as E2BSdkSandbox
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_MAX_WRITE_BATCH_FILES = 128
_MAX_WRITE_BATCH_BYTES = 8 * 1024 * 1024


def _ensure_sandbox_dir(e2b_sandbox: E2BSdkSandbox, path: str) -> None:
    """Best-effort create a directory tree inside the sandbox."""
    pure_path = PurePosixPath(path)
    if pure_path in {PurePosixPath("."), PurePosixPath("/")}:
        return

    try:
        e2b_sandbox.files.make_dir(str(pure_path))
    except Exception:
        # Some backends raise when the directory already exists. The
        # subsequent write will surface real failures if the path is bad.
        return


def _iter_write_batches(entries: list[tuple[str, bytes]]):
    """Yield bounded batches for the E2B multipart write_files API."""
    batch: list[tuple[str, bytes]] = []
    batch_bytes = 0

    for path, content in entries:
        size = len(content)
        if batch and (
            len(batch) >= _MAX_WRITE_BATCH_FILES
            or batch_bytes + size > _MAX_WRITE_BATCH_BYTES
        ):
            yield batch
            batch = []
            batch_bytes = 0

        batch.append((path, content))
        batch_bytes += size

    if batch:
        yield batch


def _sandbox_write_many(e2b_sandbox: E2BSdkSandbox, entries: list[tuple[str, bytes]]) -> int:
    """Write multiple files to the sandbox with as few API calls as practical."""
    if not entries:
        return 0

    parent_dirs = sorted(
        {
            str(parent)
            for path, _ in entries
            if (parent := PurePosixPath(path).parent) not in {PurePosixPath("."), PurePosixPath("/")}
        },
        key=lambda value: (len(PurePosixPath(value).parts), value),
    )
    for dir_path in parent_dirs:
        _ensure_sandbox_dir(e2b_sandbox, dir_path)

    written = 0
    for batch in _iter_write_batches(entries):
        e2b_sandbox.files.write_files(
            [{"path": path, "data": content} for path, content in batch]
        )
        written += len(batch)

    return written


def _sandbox_write(e2b_sandbox: E2BSdkSandbox, path: str, content: bytes) -> None:
    """Synchronous E2B files.write — intended to be called via ``asyncio.to_thread``."""
    _ensure_sandbox_dir(e2b_sandbox, str(PurePosixPath(path).parent))
    e2b_sandbox.files.write(path, content)


def _build_skills_archive(skills_root: Path) -> tuple[bytes, int]:
    """Build a tar.gz archive for the shared platform skills tree."""
    archive_buffer = io.BytesIO()
    file_count = 0

    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for category in ("public", "custom"):
            category_path = skills_root / category
            if not category_path.exists() or not category_path.is_dir():
                continue

            for current_root, dir_names, file_names in os.walk(category_path, followlinks=True):
                dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
                visible_files = sorted(name for name in file_names if not name.startswith("."))

                for file_name in visible_files:
                    source_path = Path(current_root) / file_name
                    if not source_path.is_file():
                        continue

                    relative_path = source_path.relative_to(skills_root)
                    archive.add(
                        source_path,
                        arcname=str(PurePosixPath(*relative_path.parts)),
                        recursive=False,
                    )
                    file_count += 1

    return archive_buffer.getvalue(), file_count


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


async def inject_user_uploads(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    e2b_sandbox: E2BSdkSandbox,
    path_mapping: E2BPathMapping | None = None,
) -> int:
    """Download all user uploads from BOS and write them into an E2B sandbox."""
    from crab_platform.db.repos.upload_repo import UploadRepo
    from crab_platform.storage import get_object_storage

    injected = 0
    path_mapping = path_mapping or E2BPathMapping()

    async with session_factory() as db:
        uploads = await UploadRepo(db).list_for_user(user_id)

    if not uploads:
        logger.debug("No uploads for user %s, skipping injection", user_id)
        return 0

    storage = get_object_storage()
    entries: list[tuple[str, bytes]] = []

    for upload in uploads:
        try:
            content = await storage.get(upload.bos_key)
            virtual_path = f"{path_mapping.virtual_user_data_root}/uploads/{upload.filename}"
            entries.append((path_mapping.map_path(virtual_path), content))
            injected += 1
            logger.debug("Injected %s into E2B sandbox", virtual_path)

            if upload.markdown_bos_key:
                try:
                    md_content = await storage.get(upload.markdown_bos_key)
                    md_name = f"{upload.filename}.extracted.md"
                    md_path = f"{path_mapping.virtual_user_data_root}/uploads/{md_name}"
                    entries.append((path_mapping.map_path(md_path), md_content))
                    injected += 1
                    logger.debug("Injected markdown %s into E2B sandbox", md_path)
                except Exception:
                    logger.warning("Failed to inject markdown for %s", upload.filename, exc_info=True)
        except Exception:
            logger.warning("Failed to inject %s into E2B sandbox", upload.filename, exc_info=True)

    await asyncio.to_thread(_sandbox_write_many, e2b_sandbox, entries)

    logger.info("Injected %d files into E2B sandbox for user %s", injected, user_id)
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

    entries: list[tuple[str, bytes]] = []
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
                entries.append((path_mapping.map_path(virtual_path), content))
                injected += 1
                logger.debug("Injected custom skill file %s", virtual_path)
            except Exception:
                logger.warning("Failed to inject custom skill object %s", key, exc_info=True)

    await asyncio.to_thread(_sandbox_write_many, e2b_sandbox, entries)

    return injected


async def inject_platform_skills(
    e2b_sandbox: E2BSdkSandbox,
    path_mapping: E2BPathMapping | None = None,
    skills_path: Path | None = None,
) -> int:
    """Copy shared filesystem-backed skills into the E2B sandbox."""
    from crab.config import get_app_config

    path_mapping = path_mapping or E2BPathMapping()
    if skills_path is None:
        skills_path = get_app_config().skills.get_skills_path()

    if not skills_path.exists():
        logger.debug("Skills directory %s does not exist; skipping platform skill injection", skills_path)
        return 0

    skills_root = skills_path.resolve()
    archive_bytes, injected = await asyncio.to_thread(_build_skills_archive, skills_root)
    if injected == 0:
        logger.debug("No platform skill files found in %s; skipping platform skill injection", skills_root)
        return 0

    archive_path = str(PurePosixPath(path_mapping.actual_skills_root) / ".platform-skills.tar.gz")
    await asyncio.to_thread(_sandbox_write_many, e2b_sandbox, [(archive_path, archive_bytes)])
    await asyncio.to_thread(
        e2b_sandbox.commands.run,
        (
            f"mkdir -p {shlex.quote(path_mapping.actual_skills_root)} && "
            f"tar -xzf {shlex.quote(archive_path)} -C {shlex.quote(path_mapping.actual_skills_root)} && "
            f"rm -f {shlex.quote(archive_path)}"
        ),
        timeout=300,
    )

    logger.info("Injected %d platform skill files into E2B sandbox", injected)
    return injected
