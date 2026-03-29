"""FastAPI dependency injection for authentication and request context."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth_cookies import ACCESS_TOKEN_COOKIE
from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.auth.jwt import JWTAuthProvider
from crab_platform.context import RequestContext
from crab_platform.db import get_db
from crab_platform.db.repos.thread_repo import ThreadRepo

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedUser:
    """Extract and verify the Bearer token, returning the authenticated user.

    Raises 401 if the token is missing or invalid.
    """
    token = credentials.credentials if credentials is not None else request.cookies.get(ACCESS_TOKEN_COOKIE)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provider = JWTAuthProvider(db)
    user = await provider.authenticate(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def build_request_context(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RequestContext:
    """Build an immutable RequestContext from the authenticated user and request params.

    If thread_id is present in the path, validates that the thread belongs to the user.
    """
    thread_id: uuid.UUID | None = None
    raw_thread_id = request.path_params.get("thread_id")
    if raw_thread_id:
        try:
            thread_id = uuid.UUID(str(raw_thread_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid thread_id")

        # Verify thread ownership
        thread = await ThreadRepo(db).get(thread_id)
        if thread is not None and thread.user_id != user.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Thread not owned by current user")

    # Read optional query/body params from request
    body: dict = {}
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
        except Exception:
            body = {}

    return RequestContext(
        request_id=str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        thread_id=thread_id,
        model_name=body.get("model_name") or request.query_params.get("model_name"),
        thinking_enabled=body.get("thinking_enabled", True),
        reasoning_effort=body.get("reasoning_effort"),
        is_plan_mode=body.get("is_plan_mode", False),
        subagent_enabled=body.get("subagent_enabled", False),
        max_concurrent_subagents=body.get("max_concurrent_subagents", 3),
        agent_name=body.get("agent_name"),
    )
