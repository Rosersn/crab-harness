"""Helpers for access/refresh token cookies."""

from __future__ import annotations

from fastapi import Request, Response

ACCESS_TOKEN_COOKIE = "crab_access_token"
REFRESH_TOKEN_COOKIE = "crab_refresh_token"


def _is_secure_request(request: Request | None = None) -> bool:
    """Detect whether cookies should be marked secure."""
    if request is None:
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme
    return scheme == "https"


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_max_age: int,
    refresh_max_age: int,
    request: Request | None = None,
) -> None:
    """Persist the current auth session in cookies."""
    secure = _is_secure_request(request)
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        httponly=True,
        max_age=access_max_age,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        max_age=refresh_max_age,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_auth_cookies(response: Response, request: Request | None = None) -> None:
    """Remove the persisted auth session cookies."""
    secure = _is_secure_request(request)
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/", samesite="lax", secure=secure)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/", samesite="lax", secure=secure)
