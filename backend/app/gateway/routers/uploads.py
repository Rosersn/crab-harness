"""Upload router for handling file uploads.

Files are stored in object storage (BOS/local) with metadata in PostgreSQL.
Additionally, files are written to the local thread directory and synced to
the sandbox so that UploadsMiddleware and the Agent can discover them.
This local write will become unnecessary once E2B sandbox (Phase 4) replaces
the local sandbox — at that point files will be injected from BOS directly.
"""

import asyncio

import logging
import os
import stat
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.deps import get_current_user
from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.db import get_db
from crab_platform.db.repos.thread_repo import ThreadRepo
from crab_platform.db.repos.upload_repo import UploadRepo
from crab_platform.storage import get_object_storage
from deerflow.config.paths import get_paths
from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.uploads.manager import (
    claim_unique_filename,
    ensure_uploads_dir,
    normalize_filename,
    upload_artifact_url,
    upload_virtual_path,
)
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[dict[str, str]]
    message: str


def _build_file_info(thread_id: str, sandbox_uploads: Path, upload) -> dict[str, str]:
    """Build the API response payload for an upload metadata record."""
    info: dict[str, str] = {
        "upload_id": str(upload.id),
        "filename": upload.filename,
        "size": str(upload.size_bytes),
        "path": str(sandbox_uploads / upload.filename),
        "virtual_path": upload_virtual_path(upload.filename),
        "artifact_url": upload_artifact_url(thread_id, upload.filename),
    }
    if upload.markdown_bos_key:
        md_name = f"{upload.filename}.extracted.md"
        info["markdown_file"] = md_name
        info["markdown_path"] = str(sandbox_uploads / md_name)
        info["markdown_virtual_path"] = upload_virtual_path(md_name)
        info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_name)
    return info


def _bos_key(tenant_id: uuid.UUID, user_id: uuid.UUID, thread_id: uuid.UUID, upload_id: uuid.UUID, filename: str) -> str:
    """Build the BOS object key for an uploaded file."""
    return f"{tenant_id}/{user_id}/uploads/{thread_id}/{upload_id}_{filename}"


def _bos_md_key(bos_key: str) -> str:
    """Build the BOS key for the markdown companion of an upload."""
    return f"{bos_key}.md"


def _make_file_sandbox_writable(file_path: os.PathLike[str] | str) -> None:
    """Ensure uploaded files remain writable when mounted into non-local sandboxes."""
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return
    writable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, writable_mode, **chmod_kwargs)


async def _verify_thread_ownership(thread_id: uuid.UUID, user: AuthenticatedUser, db: AsyncSession) -> None:
    """Verify the thread belongs to the current user, or does not exist yet (allow)."""
    thread = await ThreadRepo(db).get(thread_id)
    if thread is not None and thread.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Thread not owned by current user")


async def _ensure_owned_thread_exists(
    thread_id: uuid.UUID,
    user: AuthenticatedUser,
    db: AsyncSession,
) -> None:
    """Ensure a draft thread exists before persisting upload metadata.

    New conversations can upload files before the first LangGraph run creates
    the thread row. In that case we materialize the thread eagerly so the
    upload metadata FK remains valid.
    """
    repo = ThreadRepo(db)
    thread = await repo.get(thread_id)
    if thread is not None:
        if thread.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="Thread not owned by current user")
        return

    await repo.create(
        id=thread_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )


async def _acquire_sandbox_for_upload(thread_id: str):
    """Acquire the sandbox without blocking the request event loop."""
    provider = get_sandbox_provider()
    sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
    sandbox = provider.get(sandbox_id)
    return provider, sandbox_id, sandbox


async def _release_sandbox_after_upload(provider, sandbox_id: str) -> None:
    """Release the sandbox from a worker thread to avoid nested event loops."""
    await asyncio.to_thread(provider.release, sandbox_id)


async def _sync_upload_to_sandbox(sandbox, virtual_path: str, content: bytes) -> None:
    """Write uploaded content into the sandbox from a worker thread."""
    await asyncio.to_thread(sandbox.update_file, virtual_path, content)


def _write_local_upload(local_uploads_dir: Path | None, filename: str, content: bytes, sandbox_id: str) -> None:
    """Write a file into the local thread uploads dir when available."""
    if local_uploads_dir is None:
        return
    local_path = local_uploads_dir / filename
    local_path.write_bytes(content)
    if sandbox_id != "local":
        _make_file_sandbox_writable(local_path)


async def _materialize_upload_file(
    *,
    local_uploads_dir: Path | None,
    sandbox_id: str,
    sandbox,
    filename: str,
    content: bytes,
) -> None:
    """Ensure a file exists in the local thread dir and active sandbox."""
    _write_local_upload(local_uploads_dir, filename, content, sandbox_id)
    if sandbox_id != "local":
        await _sync_upload_to_sandbox(sandbox, upload_virtual_path(filename), content)


async def _existing_upload_matches(storage, upload, content: bytes) -> bool:
    """Return True when an existing upload record has identical content."""
    if upload.size_bytes != len(content):
        return False
    try:
        existing_content = await storage.get(upload.bos_key)
    except Exception:
        logger.warning("Failed to read existing upload %s for dedupe check", upload.filename, exc_info=True)
        return False
    return existing_content == content


async def _reuse_existing_upload(
    *,
    thread_id: str,
    storage,
    upload,
    local_uploads_dir: Path | None,
    sandbox_id: str,
    sandbox,
    sandbox_uploads: Path,
) -> dict[str, str]:
    """Rehydrate an existing upload into the current sandbox/local dir and return its payload."""
    content = await storage.get(upload.bos_key)
    await _materialize_upload_file(
        local_uploads_dir=local_uploads_dir,
        sandbox_id=sandbox_id,
        sandbox=sandbox,
        filename=upload.filename,
        content=content,
    )

    if upload.markdown_bos_key:
        md_content = await storage.get(upload.markdown_bos_key)
        await _materialize_upload_file(
            local_uploads_dir=local_uploads_dir,
            sandbox_id=sandbox_id,
            sandbox=sandbox,
            filename=f"{upload.filename}.extracted.md",
            content=md_content,
        )

    payload = _build_file_info(thread_id, sandbox_uploads, upload)
    payload["reused"] = "true"
    return payload


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """Upload multiple files to object storage + local thread dir with PG metadata."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        tid = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    await _ensure_owned_thread_exists(tid, user, db)

    storage = get_object_storage()
    upload_repo = UploadRepo(db)
    uploaded_files: list[dict[str, str]] = []
    existing_uploads = await upload_repo.list_for_thread(tid, user.user_id)
    existing_uploads_by_name = {u.filename: u for u in existing_uploads}
    reserved_names = set(existing_uploads_by_name)

    # Local thread directory + sandbox (for UploadsMiddleware / Agent compatibility)
    try:
        local_uploads_dir = ensure_uploads_dir(thread_id)
    except ValueError:
        local_uploads_dir = None

    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id)
    sandbox_provider, sandbox_id, sandbox = await _acquire_sandbox_for_upload(thread_id)
    if sandbox_id != "local" and sandbox is None:
        raise HTTPException(status_code=500, detail="Failed to acquire sandbox for thread uploads")

    try:
        for file in files:
            if not file.filename:
                continue

            try:
                safe_filename = normalize_filename(file.filename)
            except ValueError:
                logger.warning("Skipping file with unsafe filename: %r", file.filename)
                continue

            try:
                content = await file.read()
                existing_upload = existing_uploads_by_name.get(safe_filename)
                if existing_upload is not None and await _existing_upload_matches(storage, existing_upload, content):
                    payload = await _reuse_existing_upload(
                        thread_id=thread_id,
                        storage=storage,
                        upload=existing_upload,
                        local_uploads_dir=local_uploads_dir,
                        sandbox_id=sandbox_id,
                        sandbox=sandbox,
                        sandbox_uploads=sandbox_uploads,
                    )
                    logger.info("Reused existing upload %s for thread %s", safe_filename, tid)
                    uploaded_files.append(payload)
                    continue

                effective_filename = claim_unique_filename(safe_filename, reserved_names)
                upload_id = uuid.uuid4()
                key = _bos_key(user.tenant_id, user.user_id, tid, upload_id, effective_filename)

                # 1. Store in object storage (BOS / local storage backend)
                content_type = file.content_type or "application/octet-stream"
                await storage.put(key, content, content_type=content_type)

                # 2. Write to local thread directory (for UploadsMiddleware + sandbox compat)
                await _materialize_upload_file(
                    local_uploads_dir=local_uploads_dir,
                    sandbox_id=sandbox_id,
                    sandbox=sandbox,
                    filename=effective_filename,
                    content=content,
                )

                file_info: dict[str, str] = {
                    "filename": effective_filename,
                    "size": str(len(content)),
                    "path": str(sandbox_uploads / effective_filename),
                    "virtual_path": upload_virtual_path(effective_filename),
                    "artifact_url": upload_artifact_url(thread_id, effective_filename),
                }

                markdown_bos_key: str | None = None

                # 4. Convert document to markdown if applicable
                file_ext = Path(effective_filename).suffix.lower()
                if file_ext in CONVERTIBLE_EXTENSIONS:
                    # Use local file if available, otherwise temp file
                    convert_source = local_uploads_dir / effective_filename if local_uploads_dir else None
                    tmp_path: Path | None = None
                    if convert_source is None or not convert_source.exists():
                        tmp = tempfile.NamedTemporaryFile(suffix=file_ext, delete=False)
                        tmp.write(content)
                        tmp.close()
                        convert_source = Path(tmp.name)
                        tmp_path = convert_source

                    try:
                        md_path = await convert_file_to_markdown(convert_source)
                        if md_path:
                            md_content = md_path.read_bytes()
                            md_key = _bos_md_key(key)
                            await storage.put(md_key, md_content, content_type="text/markdown")
                            markdown_bos_key = md_key

                            md_name = f"{effective_filename}.extracted.md"
                            md_virtual_path = upload_virtual_path(md_name)
                            await _materialize_upload_file(
                                local_uploads_dir=local_uploads_dir,
                                sandbox_id=sandbox_id,
                                sandbox=sandbox,
                                filename=md_name,
                                content=md_content,
                            )

                            file_info["markdown_file"] = md_name
                            file_info["markdown_path"] = str(sandbox_uploads / md_name)
                            file_info["markdown_virtual_path"] = md_virtual_path
                            file_info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_name)

                            # Clean up temp markdown if it's not in uploads dir
                            if md_path.parent != local_uploads_dir:
                                md_path.unlink(missing_ok=True)
                    finally:
                        if tmp_path is not None:
                            tmp_path.unlink(missing_ok=True)

                # 5. Write metadata to PG
                record = await upload_repo.create(
                    thread_id=tid,
                    user_id=user.user_id,
                    tenant_id=user.tenant_id,
                    filename=effective_filename,
                    size_bytes=len(content),
                    bos_key=key,
                    markdown_bos_key=markdown_bos_key,
                )
                file_info["upload_id"] = str(record.id)
                existing_uploads_by_name[effective_filename] = record

                logger.info("Uploaded %s (%d bytes) → %s", effective_filename, len(content), key)
                uploaded_files.append(file_info)

            except HTTPException:
                raise
            except Exception as e:
                logger.error("Failed to upload %s: %s", file.filename, e)
                raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {e}")
    finally:
        # Always release the sandbox to avoid keeping it pinned
        try:
            await _release_sandbox_after_upload(sandbox_provider, sandbox_id)
        except Exception:
            logger.debug("Failed to release sandbox %s after upload", sandbox_id, exc_info=True)

    await db.commit()

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


@router.get("/list", response_model=dict)
async def list_uploaded_files(
    thread_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all uploaded files for a thread from PG metadata."""
    try:
        tid = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    await _verify_thread_ownership(tid, user, db)

    uploads = await UploadRepo(db).list_for_thread(tid, user.user_id)
    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id)
    files = []
    for u in uploads:
        info: dict[str, str] = {
            "upload_id": str(u.id),
            "filename": u.filename,
            "size": str(u.size_bytes),
            "path": str(sandbox_uploads / u.filename),
            "virtual_path": upload_virtual_path(u.filename),
            "artifact_url": upload_artifact_url(thread_id, u.filename),
        }
        if u.markdown_bos_key:
            md_name = f"{u.filename}.extracted.md"
            info["markdown_file"] = md_name
            info["markdown_path"] = str(sandbox_uploads / md_name)
            info["markdown_virtual_path"] = upload_virtual_path(md_name)
            info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_name)
        files.append(info)

    return {"files": files, "count": len(files)}


@router.delete("/{filename}")
async def delete_uploaded_file(
    thread_id: str,
    filename: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an uploaded file from object storage, local dir, and PG metadata."""
    try:
        tid = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    await _verify_thread_ownership(tid, user, db)

    upload_repo = UploadRepo(db)
    record = await upload_repo.get_by_filename(tid, user.user_id, filename)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    storage = get_object_storage()

    # Delete from object storage (best-effort — PG is source of truth)
    try:
        await storage.delete(record.bos_key)
        if record.markdown_bos_key:
            await storage.delete(record.markdown_bos_key)
    except Exception:
        logger.warning("Failed to delete object storage key %s (continuing)", record.bos_key)

    # Delete from local thread directory (best-effort)
    try:
        from deerflow.uploads.manager import get_uploads_dir
        local_dir = get_uploads_dir(thread_id)
        local_file = local_dir / filename
        if local_file.is_file():
            local_file.unlink()
            # Also clean up companion markdown
            if record.markdown_bos_key:
                md_file = local_dir / f"{filename}.extracted.md"
                md_file.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to delete local file %s (continuing)", filename)

    # Delete from sandbox if a thread-bound sandbox already exists
    try:
        thread = await ThreadRepo(db).get(tid)
        if thread and thread.sandbox_id:
            sandbox_provider = get_sandbox_provider()
            sandbox = sandbox_provider.get(thread.sandbox_id)
            if sandbox is not None:
                sandbox.execute_command(
                    "rm -f "
                    + shlex.quote(upload_virtual_path(filename))
                    + " "
                    + shlex.quote(upload_virtual_path(f"{filename}.extracted.md"))
                )
    except Exception:
        logger.warning("Failed to delete sandbox copy for %s (continuing)", filename, exc_info=True)

    # Delete from PG
    await upload_repo.delete(record.id)
    await db.commit()

    return {"success": True, "message": f"Deleted {filename}"}
