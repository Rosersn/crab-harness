"""Per-user skill loading.

Merges platform public skills with user-level enable/disable overrides
from PostgreSQL, replacing the global load_skills() + extensions_config path.

Custom user skills (stored in BOS) are included when they have a bos_key
and are enabled.  Their SKILL.md content is accessible to the agent via the
sandbox virtual path /mnt/skills/custom/{skill_name}/SKILL.md.
"""

from __future__ import annotations

import copy
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.repos.skill_config_repo import SkillConfigRepo

if TYPE_CHECKING:
    from crab.skills.types import Skill

logger = logging.getLogger(__name__)


async def load_user_skills(
    db: AsyncSession,
    user_id: uuid.UUID,
    enabled_only: bool = True,
) -> list[Skill]:
    """Load skills for a user, merging platform skills with user overrides.

    Platform public skills come from the filesystem (via harness load_skills).
    User overrides (enable/disable per skill_name) come from PG.  If a user has
    an override for a skill, it takes precedence; otherwise the platform default
    applies.

    Custom user skills (with bos_key) are appended as Skill objects pointing to
    a virtual path under /mnt/skills/custom/.

    Returns the filtered skill list.
    """
    from crab.skills import load_skills
    from crab.skills.types import Skill as SkillType

    # 1. Load platform skills (all, regardless of enabled state)
    #    Deep-copy to avoid mutating shared objects if load_skills() ever caches.
    platform_skills = copy.deepcopy(load_skills(enabled_only=False))

    # 2. Load user overrides from PG
    repo = SkillConfigRepo(db)
    user_configs = await repo.list_for_user(user_id)
    overrides = {c.skill_name: c for c in user_configs}

    # 3. Apply user overrides to platform skills
    result: list[Skill] = []
    for skill in platform_skills:
        if skill.name in overrides:
            skill.enabled = overrides[skill.name].enabled

        if enabled_only and not skill.enabled:
            continue
        result.append(skill)

    # 4. Add custom user skills (those with a bos_key) that aren't already
    #    in the platform set.
    platform_names = {s.name for s in platform_skills}
    for config in user_configs:
        if config.skill_name in platform_names:
            continue  # Already handled above
        if not config.bos_key:
            continue  # Not a custom skill
        if enabled_only and not config.enabled:
            continue

        # Build a Skill object pointing to the virtual sandbox path.
        # The actual SKILL.md is injected from BOS into the E2B sandbox
        # at /mnt/skills/custom/{skill_name}/ by the file injector.
        custom_skill = SkillType(
            name=config.skill_name,
            description=f"Custom skill: {config.skill_name}",
            license=None,
            skill_dir=Path(f"/mnt/skills/custom/{config.skill_name}"),
            skill_file=Path(f"/mnt/skills/custom/{config.skill_name}/SKILL.md"),
            relative_path=Path(config.skill_name),
            category="custom",
            enabled=config.enabled,
        )
        result.append(custom_skill)

    return result


async def get_user_enabled_skill_names(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> set[str]:
    """Get the set of enabled skill names for a user.

    Used to filter which skills appear in the system prompt.
    """
    skills = await load_user_skills(db, user_id, enabled_only=True)
    return {s.name for s in skills}
