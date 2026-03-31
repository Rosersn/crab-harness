"""Custom agent routes.

Custom filesystem-backed agents are intentionally disabled in cloud mode.
They were designed for single-user local usage and are not tenant-scoped.
"""

from crab_platform.auth.interface import AuthenticatedUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user

router = APIRouter(prefix="/api", tags=["agents"])
_CLOUD_DISABLED_DETAIL = (
    "Custom agents and the global USER.md profile are not supported in cloud mode."
)


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    soul: str | None = Field(default=None, description="SOUL.md content (included on GET /{name})")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")


def _raise_cloud_disabled() -> None:
    """Reject legacy single-user custom-agent endpoints in cloud mode."""
    raise HTTPException(status_code=410, detail=_CLOUD_DISABLED_DETAIL)


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory.",
)
async def list_agents(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AgentsListResponse:
    _raise_cloud_disabled()


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(
    name: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    _raise_cloud_disabled()


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent.",
)
async def get_agent(
    name: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AgentResponse:
    _raise_cloud_disabled()


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(
    request: AgentCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AgentResponse:
    _raise_cloud_disabled()


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(
    name: str,
    request: AgentUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AgentResponse:
    _raise_cloud_disabled()


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents.",
)
async def get_user_profile(
    user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfileResponse:
    _raise_cloud_disabled()


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents.",
)
async def update_user_profile(
    request: UserProfileUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfileResponse:
    _raise_cloud_disabled()


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, SOUL.md, memory).",
)
async def delete_agent(
    name: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    _raise_cloud_disabled()
