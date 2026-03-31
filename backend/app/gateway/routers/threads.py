import logging

from crab_platform.auth.interface import AuthenticatedUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.gateway.deps import get_current_user
from crab.config.paths import Paths, get_paths

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadDeleteResponse(BaseModel):
    """Response model for thread cleanup."""

    success: bool
    message: str


def _delete_thread_data(thread_id: str, paths: Paths | None = None) -> ThreadDeleteResponse:
    """Delete local persisted filesystem data for a thread."""
    path_manager = paths or get_paths()
    try:
        path_manager.delete_thread_dir(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete thread data for %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to delete local thread data.") from exc

    logger.info("Deleted local thread data for %s", thread_id)
    return ThreadDeleteResponse(success=True, message=f"Deleted local thread data for {thread_id}")


@router.delete("/{thread_id}", response_model=ThreadDeleteResponse)
async def delete_thread_data(
    thread_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ThreadDeleteResponse:
    """Deprecated legacy endpoint.

    Thread deletion is handled by ``/api/langgraph/threads/{thread_id}``, which
    now also performs external resource cleanup. This route stays as a hard
    failure so old clients do not silently bypass the supported path.
    """
    raise HTTPException(
        status_code=410,
        detail="Use DELETE /api/langgraph/threads/{thread_id}; local thread cleanup is no longer a separate public API.",
    )
