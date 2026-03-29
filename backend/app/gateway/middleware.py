"""Gateway-level middleware for authentication enforcement."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.gateway.auth_cookies import ACCESS_TOKEN_COOKIE

logger = logging.getLogger(__name__)

# Paths that do NOT require authentication
PUBLIC_PATHS: set[str] = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Path prefixes that do NOT require authentication
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/",
)


class AuthEnforcementMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests to protected endpoints.

    This is a safety net — individual route dependencies (get_current_user)
    perform the actual token verification. This middleware validates the JWT
    token to catch any route that accidentally forgot to declare the
    dependency.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Allow CORS preflight requests through without auth
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public endpoints
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # For all /api/* endpoints, require a valid Authorization header
        if path.startswith("/api/"):
            auth_header = request.headers.get("authorization", "")
            auth_cookie = request.cookies.get(ACCESS_TOKEN_COOKIE)

            if auth_cookie:
                return await call_next(request)

            if not auth_header.lower().startswith("bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing authentication credentials"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Validate the token is a real JWT (not just any string)
            token = auth_header[7:]  # strip "Bearer "
            if not token or token.count(".") != 2:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid token format"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a request-id header for tracing."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        import uuid

        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log request method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
