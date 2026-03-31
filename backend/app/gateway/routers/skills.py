"""Skills configuration router — per-user skill management.

Platform-level public skills (from filesystem skills/public/) are shared across all users.
Per-user skill enable/disable state and custom skills are stored in PostgreSQL.
"""

import logging

from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.db import get_db
from crab_platform.db.repos.skill_config_repo import SkillConfigRepo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.deps import get_current_user
from crab.skills import Skill, load_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ── Response/Request schemas ──────────────────────────────────────────


class SkillResponse(BaseModel):
    """Response model for skill information."""

    name: str
    description: str
    license: str | None = None
    category: str
    enabled: bool = True


class SkillsListResponse(BaseModel):
    """Response model for listing all skills."""

    skills: list[SkillResponse]


class UserSkillResponse(BaseModel):
    """Response model for a user-level skill config."""

    skill_name: str
    enabled: bool
    bos_key: str | None = None


class UserSkillListResponse(BaseModel):
    """Combined platform + user skill listing."""

    platform_skills: list[SkillResponse] = Field(default_factory=list, description="Platform public skills")
    user_configs: list[UserSkillResponse] = Field(default_factory=list, description="User skill enable/disable overrides and custom skills")


class SkillUpdateRequest(BaseModel):
    """Request model for updating a skill."""

    enabled: bool


class SkillInstallResponse(BaseModel):
    """Response model for skill installation."""

    success: bool
    skill_name: str
    message: str


def _skill_to_response(skill: Skill) -> SkillResponse:
    """Convert a Skill object to a SkillResponse."""
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        license=skill.license,
        category=skill.category,
        enabled=skill.enabled,
    )


# ── Platform-level skills (read-only) ────────────────────────────────


@router.get(
    "",
    response_model=SkillsListResponse,
    summary="List Platform Skills",
    description="Retrieve all platform-level skills from filesystem.",
)
async def list_skills(
    user: AuthenticatedUser = Depends(get_current_user),
) -> SkillsListResponse:
    """List all platform-level skills."""
    try:
        skills = load_skills(enabled_only=False)
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
    except Exception as e:
        logger.error("Failed to load skills: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load skills: {e}")


# ── Per-user skill config CRUD ────────────────────────────────────────
# IMPORTANT: These routes with fixed prefixes (/user/configs) MUST be declared
# BEFORE the /{skill_name} catch-all, otherwise FastAPI matches "user" as skill_name.


@router.get(
    "/user/configs",
    response_model=UserSkillListResponse,
    summary="List All Skills With User Overrides",
    description="List platform skills + user-level enable/disable overrides and custom skills.",
)
async def list_user_skills(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserSkillListResponse:
    """List platform skills + user overrides."""
    # Platform skills
    try:
        platform = load_skills(enabled_only=False)
        platform_skills = [_skill_to_response(s) for s in platform]
    except Exception:
        platform_skills = []

    # User configs
    repo = SkillConfigRepo(db)
    user_configs_raw = await repo.list_for_user(user.user_id)
    user_configs = [
        UserSkillResponse(
            skill_name=c.skill_name,
            enabled=c.enabled,
            bos_key=c.bos_key,
        )
        for c in user_configs_raw
    ]

    return UserSkillListResponse(platform_skills=platform_skills, user_configs=user_configs)


@router.put(
    "/user/configs/{skill_name}",
    response_model=UserSkillResponse,
    summary="Set User Skill Config",
    description="Enable or disable a skill for the current user.",
)
async def upsert_user_skill_config(
    skill_name: str,
    request: SkillUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserSkillResponse:
    """Create or update a user-level skill config (enable/disable)."""
    repo = SkillConfigRepo(db)
    record = await repo.upsert(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        skill_name=skill_name,
        enabled=request.enabled,
    )
    await db.commit()

    return UserSkillResponse(
        skill_name=record.skill_name,
        enabled=record.enabled,
        bos_key=record.bos_key,
    )


@router.delete(
    "/user/configs/{skill_name}",
    summary="Delete User Skill Config",
    description="Remove a user-level skill configuration override.",
)
async def delete_user_skill_config(
    skill_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a user-level skill config."""
    repo = SkillConfigRepo(db)
    deleted = await repo.delete(user.user_id, skill_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill config '{skill_name}' not found")

    await db.commit()
    return {"success": True, "message": f"Deleted skill config '{skill_name}'"}


# ── Platform skill detail (catch-all, MUST be last) ──────────────────


@router.get(
    "/{skill_name}",
    response_model=SkillResponse,
    summary="Get Skill Details",
    description="Retrieve detailed information about a specific platform skill.",
)
async def get_skill(
    skill_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> SkillResponse:
    """Get details of a platform-level skill."""
    try:
        skills = load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        return _skill_to_response(skill)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get skill: {e}")
