import asyncio
import logging
import mimetypes
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.deps import get_current_user
from app.gateway.path_utils import resolve_thread_virtual_path
from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.db import get_db
from crab_platform.db.models import Thread
from crab_platform.db.repos.thread_repo import ThreadRepo
from deerflow.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

ACTIVE_CONTENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}


def _build_content_disposition(disposition_type: str, filename: str) -> str:
    """Build an RFC 5987 encoded Content-Disposition header value."""
    return f"{disposition_type}; filename*=UTF-8''{quote(filename)}"


def _build_attachment_headers(filename: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Content-Disposition": _build_content_disposition("attachment", filename)}
    if extra_headers:
        headers.update(extra_headers)
    return headers


def is_text_file_by_content(path: Path, sample_size: int = 8192) -> bool:
    """Check if file is text by examining content for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
            # Text files shouldn't contain null bytes
            return b"\x00" not in chunk
    except Exception:
        return False


def _extract_file_from_skill_archive(zip_path: Path, internal_path: str) -> bytes | None:
    """Extract a file from a .skill ZIP archive.

    Args:
        zip_path: Path to the .skill file (ZIP archive).
        internal_path: Path to the file inside the archive (e.g., "SKILL.md").

    Returns:
        The file content as bytes, or None if not found.
    """
    if not zipfile.is_zipfile(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # List all files in the archive
            namelist = zip_ref.namelist()

            # Try direct path first
            if internal_path in namelist:
                return zip_ref.read(internal_path)

            # Try with any top-level directory prefix (e.g., "skill-name/SKILL.md")
            for name in namelist:
                if name.endswith("/" + internal_path) or name == internal_path:
                    return zip_ref.read(name)

            # Not found
            return None
    except (zipfile.BadZipFile, KeyError):
        return None


def _extract_file_from_skill_archive_bytes(zip_bytes: bytes, internal_path: str) -> bytes | None:
    """Extract a file from raw .skill ZIP bytes."""
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zip_ref:
            namelist = zip_ref.namelist()
            if internal_path in namelist:
                return zip_ref.read(internal_path)
            for name in namelist:
                if name.endswith("/" + internal_path) or name == internal_path:
                    return zip_ref.read(name)
    except (zipfile.BadZipFile, KeyError):
        return None
    return None


def _looks_like_text_bytes(content: bytes, sample_size: int = 8192) -> bool:
    """Best-effort text detection for remote artifact content."""
    return b"\x00" not in content[:sample_size]


def _normalize_virtual_path(virtual_path: str) -> str:
    return "/" + virtual_path.lstrip("/")


async def _read_remote_artifact_bytes(thread_id: str, virtual_path: str, sandbox_id: str | None) -> bytes | None:
    """Best-effort read of an artifact directly from the sandbox."""
    if not sandbox_id:
        return None

    provider = get_sandbox_provider()
    acquired_sandbox_id: str | None = None
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        try:
            acquired_sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
            sandbox = provider.get(acquired_sandbox_id)
        except Exception:
            logger.debug("Failed to acquire sandbox for artifact fallback", exc_info=True)
            return None

    if sandbox is None:
        return None

    try:
        sandbox_path = _normalize_virtual_path(virtual_path)
        if hasattr(sandbox, "read_bytes"):
            return await asyncio.to_thread(sandbox.read_bytes, sandbox_path)
        return (await asyncio.to_thread(sandbox.read_file, sandbox_path)).encode("utf-8")
    except Exception:
        logger.debug("Failed to read remote artifact %s from sandbox %s", virtual_path, sandbox_id, exc_info=True)
        return None
    finally:
        if acquired_sandbox_id is not None:
            try:
                await asyncio.to_thread(provider.release, acquired_sandbox_id)
            except Exception:
                logger.debug("Failed to release sandbox %s after artifact fallback", acquired_sandbox_id, exc_info=True)


async def _require_owned_thread(thread_id: str, user: AuthenticatedUser, db: AsyncSession) -> Thread:
    """Reject artifact access unless the thread belongs to the current user."""
    try:
        parsed_thread_id = uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread_id") from exc

    thread = await ThreadRepo(db).get(parsed_thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Thread not owned by current user")
    return thread


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="Get Artifact File",
    description="Retrieve an artifact file generated by the AI agent. Text and binary files can be viewed inline, while active web content is always downloaded.",
)
async def get_artifact(
    thread_id: str,
    path: str,
    request: Request,
    download: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Get an artifact file by its path.

    The endpoint automatically detects file types and returns appropriate content types.
    Use the `download` query parameter to force file download for non-active content.

    Args:
        thread_id: The thread ID.
        path: The artifact path with virtual prefix (e.g., mnt/user-data/outputs/file.txt).
        request: FastAPI request object (automatically injected).

    Returns:
        The file content as a FileResponse with appropriate content type:
        - Active content (HTML/XHTML/SVG): Served as download attachment
        - Text files: Plain text with proper MIME type
        - Binary files: Inline display with download option

    Raises:
        HTTPException:
            - 400 if path is invalid or not a file
            - 403 if access denied (path traversal detected)
            - 404 if file not found

    Query Parameters:
        download (bool): If true, forces attachment download for file types that are
            otherwise returned inline or as plain text. Active HTML/XHTML/SVG content
            is always downloaded regardless of this flag.

    Example:
        - Get text file inline: `/api/threads/abc123/artifacts/mnt/user-data/outputs/notes.txt`
        - Download file: `/api/threads/abc123/artifacts/mnt/user-data/outputs/data.csv?download=true`
        - Active web content such as `.html`, `.xhtml`, and `.svg` artifacts is always downloaded
    """
    thread = await _require_owned_thread(thread_id, user, db)

    # Check if this is a request for a file inside a .skill archive (e.g., xxx.skill/SKILL.md)
    if ".skill/" in path:
        # Split the path at ".skill/" to get the ZIP file path and internal path
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]  # e.g., "mnt/user-data/outputs/my-skill.skill"
        internal_path = path[marker_pos + len(skill_marker) :]  # e.g., "SKILL.md"

        actual_skill_path = resolve_thread_virtual_path(thread_id, skill_file_path)

        content: bytes | None
        if actual_skill_path.exists():
            if not actual_skill_path.is_file():
                raise HTTPException(status_code=400, detail=f"Path is not a file: {skill_file_path}")
            content = _extract_file_from_skill_archive(actual_skill_path, internal_path)
        else:
            remote_skill_bytes = await _read_remote_artifact_bytes(thread_id, skill_file_path, thread.sandbox_id)
            if remote_skill_bytes is None:
                raise HTTPException(status_code=404, detail=f"Skill file not found: {skill_file_path}")
            content = _extract_file_from_skill_archive_bytes(remote_skill_bytes, internal_path)

        if content is None:
            raise HTTPException(status_code=404, detail=f"File '{internal_path}' not found in skill archive")

        # Determine MIME type based on the internal file
        mime_type, _ = mimetypes.guess_type(internal_path)
        # Add cache headers to avoid repeated ZIP extraction (cache for 5 minutes)
        cache_headers = {"Cache-Control": "private, max-age=300"}
        download_name = Path(internal_path).name or actual_skill_path.stem
        if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=_build_attachment_headers(download_name, cache_headers))

        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(content=content.decode("utf-8"), media_type=mime_type, headers=cache_headers)

        # Default to plain text for unknown types that look like text
        try:
            return PlainTextResponse(content=content.decode("utf-8"), media_type="text/plain", headers=cache_headers)
        except UnicodeDecodeError:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=cache_headers)

    actual_path = resolve_thread_virtual_path(thread_id, path)

    logger.info(f"Resolving artifact path: thread_id={thread_id}, requested_path={path}, actual_path={actual_path}")

    remote_content: bytes | None = None
    if not actual_path.exists():
        remote_content = await _read_remote_artifact_bytes(thread_id, path, thread.sandbox_id)
        if remote_content is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")

    if actual_path.exists() and not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    filename = actual_path.name if actual_path.exists() else Path(path).name
    mime_type, _ = mimetypes.guess_type(filename)

    if download:
        if remote_content is not None:
            return Response(content=remote_content, media_type=mime_type or "application/octet-stream", headers=_build_attachment_headers(filename))
        return FileResponse(path=actual_path, filename=actual_path.name, media_type=mime_type, headers=_build_attachment_headers(actual_path.name))

    # Always force download for active content types to prevent script execution
    # in the application origin when users open generated artifacts.
    if mime_type in ACTIVE_CONTENT_MIME_TYPES:
        if remote_content is not None:
            return Response(content=remote_content, media_type=mime_type or "application/octet-stream", headers=_build_attachment_headers(filename))
        return FileResponse(path=actual_path, filename=actual_path.name, media_type=mime_type, headers=_build_attachment_headers(actual_path.name))

    if remote_content is not None:
        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(content=remote_content.decode("utf-8"), media_type=mime_type)
        if _looks_like_text_bytes(remote_content):
            return PlainTextResponse(content=remote_content.decode("utf-8"), media_type=mime_type or "text/plain")
        return Response(content=remote_content, media_type=mime_type, headers={"Content-Disposition": _build_content_disposition("inline", filename)})

    if mime_type and mime_type.startswith("text/"):
        return PlainTextResponse(content=actual_path.read_text(encoding="utf-8"), media_type=mime_type)

    if is_text_file_by_content(actual_path):
        return PlainTextResponse(content=actual_path.read_text(encoding="utf-8"), media_type=mime_type)

    return Response(content=actual_path.read_bytes(), media_type=mime_type, headers={"Content-Disposition": _build_content_disposition("inline", actual_path.name)})
