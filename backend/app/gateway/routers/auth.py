"""Auth routes: register, login, refresh, me."""

from __future__ import annotations

import logging
from datetime import timedelta

from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.auth.jwt import JWTAuthProvider
from crab_platform.config.platform_config import get_platform_config
from crab_platform.db import get_db
from crab_platform.db.repos.user_repo import UserRepo
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth_cookies import REFRESH_TOKEN_COOKIE, clear_auth_cookies, set_auth_cookies
from app.gateway.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response schemas ──────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    role: str


class LogoutResponse(BaseModel):
    success: bool = True


# ── Endpoints ────────────────────────────────────────────────────────────

def _apply_session_cookies(response: Response, request: Request, access_token: str, refresh_token: str) -> None:
    """Persist the token pair as cookies for browser clients."""
    config = get_platform_config()
    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        access_max_age=int(timedelta(minutes=config.jwt_access_token_expire_minutes).total_seconds()),
        refresh_max_age=int(timedelta(days=config.jwt_refresh_token_expire_days).total_seconds()),
        request=request,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user (creates a personal tenant)."""
    repo = UserRepo(db)

    # Check duplicate email
    existing = await repo.get_by_email(body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Create tenant + user
    tenant_name = body.tenant_name or body.email.split("@")[0]
    slug = tenant_name.lower().replace(" ", "-")
    tenant = await repo.create_tenant(name=tenant_name, slug=slug)
    user = await repo.create_user(
        tenant_id=tenant.id,
        email=body.email,
        password=body.password,
        role="admin",
    )
    await db.commit()

    # Issue tokens
    provider = JWTAuthProvider(db)
    access = provider._create_access_token(user)
    refresh = provider._create_refresh_token(user)
    _apply_session_cookies(response, request, access, refresh)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email and password."""
    provider = JWTAuthProvider(db)
    result = await provider.login(body.email, body.password)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    access, refresh = result
    _apply_session_cookies(response, request, access, refresh)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a refresh token for a new token pair."""
    provider = JWTAuthProvider(db)
    refresh_token = body.refresh_token if body is not None else None
    if not refresh_token:
        refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    result = await provider.refresh(refresh_token)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    access, new_refresh = result
    _apply_session_cookies(response, request, access, new_refresh)
    return TokenResponse(access_token=access, refresh_token=new_refresh)


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, response: Response) -> LogoutResponse:
    """Clear the current browser auth session."""
    clear_auth_cookies(response, request)
    return LogoutResponse()


@router.get("/me", response_model=UserResponse)
async def me(user: AuthenticatedUser = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return UserResponse(
        user_id=str(user.user_id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        role=user.role,
    )
