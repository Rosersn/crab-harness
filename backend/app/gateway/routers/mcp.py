"""MCP configuration router — per-user MCP server management.

Platform-level MCP servers (from config.yaml/extensions_config.json) are read-only
and shared across all users.  Per-user MCP servers are stored in PostgreSQL and
managed via CRUD endpoints here.
"""

import logging
from typing import Literal

from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.db import get_db
from crab_platform.db.repos.mcp_config_repo import McpConfigRepo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.deps import get_current_user
from crab.config.extensions_config import get_extensions_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# ── Response/Request schemas ──────────────────────────────────────────


class McpOAuthConfigResponse(BaseModel):
    """OAuth configuration for an MCP server (secrets redacted)."""

    enabled: bool = Field(default=True, description="Whether OAuth token injection is enabled")
    token_url: str = Field(default="", description="OAuth token endpoint URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(default="client_credentials")
    client_id: str | None = None
    # Secrets are write-only — never exposed in read responses
    client_secret: str | None = Field(default=None, exclude=True)
    refresh_token: str | None = Field(default=None, exclude=True)
    scope: str | None = None
    audience: str | None = None


class McpServerConfigResponse(BaseModel):
    """Response model for MCP server configuration (secrets redacted)."""

    enabled: bool = True
    type: str = Field(default="stdio", description="Transport type: 'stdio', 'sse', or 'http'")
    # command/args/env are server-side only — never expose to users
    url: str | None = None
    oauth: McpOAuthConfigResponse | None = None
    description: str = ""


class McpConfigResponse(BaseModel):
    """Response model for MCP configuration."""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(default_factory=dict)


class UserMcpServerRequest(BaseModel):
    """Request model for creating/updating a user-level MCP server."""

    enabled: bool = True
    transport_type: str = Field(..., description="Transport type: 'http' or 'sse' (stdio not allowed for user MCP)")
    config: dict = Field(default_factory=dict, description="Server config (url, headers, oauth, etc.)")


class UserMcpServerResponse(BaseModel):
    """Response model for a user-level MCP server."""

    server_name: str
    enabled: bool
    transport_type: str
    config: dict


class UserMcpListResponse(BaseModel):
    """Response for listing user MCP servers."""

    platform_servers: dict[str, McpServerConfigResponse] = Field(default_factory=dict, description="Platform-level (shared) MCP servers")
    user_servers: list[UserMcpServerResponse] = Field(default_factory=list, description="User-level MCP servers")


def _safe_mcp_server_response(name: str, server) -> McpServerConfigResponse:
    """Build a McpServerConfigResponse from a config model, stripping secrets."""
    oauth_resp = None
    oauth = getattr(server, "oauth", None)
    if oauth:
        oauth_resp = McpOAuthConfigResponse(
            enabled=getattr(oauth, "enabled", True),
            token_url=getattr(oauth, "token_url", ""),
            grant_type=getattr(oauth, "grant_type", "client_credentials"),
            client_id=getattr(oauth, "client_id", None),
            scope=getattr(oauth, "scope", None),
            audience=getattr(oauth, "audience", None),
        )
    return McpServerConfigResponse(
        enabled=getattr(server, "enabled", True),
        type=getattr(server, "type", "stdio"),
        url=getattr(server, "url", None),
        oauth=oauth_resp,
        description=getattr(server, "description", ""),
    )


# ── Platform-level (read-only) ────────────────────────────────────────


@router.get(
    "/config",
    response_model=McpConfigResponse,
    summary="Get Platform MCP Configuration",
    description="Retrieve platform-level MCP server configurations (shared, read-only).",
)
async def get_mcp_configuration(
    user: AuthenticatedUser = Depends(get_current_user),
) -> McpConfigResponse:
    """Get platform-level MCP configuration (shared across all users)."""
    config = get_extensions_config()
    return McpConfigResponse(
        mcp_servers={
            name: _safe_mcp_server_response(name, server)
            for name, server in config.mcp_servers.items()
        }
    )


# ── Per-user CRUD ─────────────────────────────────────────────────────


@router.get(
    "/servers",
    response_model=UserMcpListResponse,
    summary="List All MCP Servers",
    description="List both platform-level (shared) and user-level MCP servers.",
)
async def list_mcp_servers(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserMcpListResponse:
    """List all MCP servers available to the current user."""
    # Platform-level
    config = get_extensions_config()
    platform = {
        name: _safe_mcp_server_response(name, server)
        for name, server in config.mcp_servers.items()
    }

    # User-level
    repo = McpConfigRepo(db)
    user_configs = await repo.list_for_user(user.user_id)
    user_servers = [
        UserMcpServerResponse(
            server_name=c.server_name,
            enabled=c.enabled,
            transport_type=c.transport_type,
            config=c.config if isinstance(c.config, dict) else {},
        )
        for c in user_configs
    ]

    return UserMcpListResponse(platform_servers=platform, user_servers=user_servers)


@router.put(
    "/servers/{server_name}",
    response_model=UserMcpServerResponse,
    summary="Create/Update User MCP Server",
    description="Create or update a user-level MCP server configuration. Only HTTP/SSE transports allowed.",
)
async def upsert_user_mcp_server(
    server_name: str,
    request: UserMcpServerRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserMcpServerResponse:
    """Create or update a user-level MCP server."""
    repo = McpConfigRepo(db)

    try:
        record = await repo.upsert(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            server_name=server_name,
            enabled=request.enabled,
            transport_type=request.transport_type,
            config=request.config,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    return UserMcpServerResponse(
        server_name=record.server_name,
        enabled=record.enabled,
        transport_type=record.transport_type,
        config=record.config if isinstance(record.config, dict) else {},
    )


@router.delete(
    "/servers/{server_name}",
    summary="Delete User MCP Server",
    description="Delete a user-level MCP server configuration.",
)
async def delete_user_mcp_server(
    server_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a user-level MCP server."""
    repo = McpConfigRepo(db)
    deleted = await repo.delete(user.user_id, server_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_name}' not found")

    await db.commit()
    return {"success": True, "message": f"Deleted MCP server '{server_name}'"}
