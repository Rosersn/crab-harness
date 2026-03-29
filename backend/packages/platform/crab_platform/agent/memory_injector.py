"""Per-user memory injection for system prompt.

Loads memory from PG via PGMemoryStorage and formats for prompt injection,
replacing the global _get_memory_context() / get_memory_data() path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.repos.memory_repo import MemoryRepo

logger = logging.getLogger(__name__)


async def load_user_memory(
    db: AsyncSession,
    user_id: uuid.UUID,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Load memory data for a user from PostgreSQL.

    Returns the raw memory dict (same shape as FileMemoryStorage), or an empty
    memory structure if none exists.
    """
    from deerflow.agents.memory.storage import create_empty_memory

    repo = MemoryRepo(db)
    data = await repo.load(user_id, agent_name)
    if data is None:
        return create_empty_memory()
    return data


async def format_user_memory_context(
    db: AsyncSession,
    user_id: uuid.UUID,
    agent_name: str | None = None,
    max_injection_tokens: int = 2000,
) -> str:
    """Load user memory and format it for injection into the system prompt.

    Returns a string wrapped in <memory>...</memory> tags, or "" if memory
    is empty or injection is disabled.  This replaces the harness-level
    ``_get_memory_context(agent_name)`` for multi-tenant mode.
    """
    try:
        from deerflow.agents.memory import format_memory_for_injection
        from deerflow.config.memory_config import get_memory_config

        config = get_memory_config()
        if not config.enabled or not config.injection_enabled:
            return ""

        effective_max_tokens = getattr(config, "max_injection_tokens", max_injection_tokens)

        memory_data = await load_user_memory(db, user_id, agent_name)
        memory_content = format_memory_for_injection(
            memory_data, max_tokens=effective_max_tokens,
        )

        if not memory_content.strip():
            return ""

        return f"\n<memory>\n{memory_content}\n</memory>\n"
    except Exception:
        logger.exception("Failed to load user memory for %s", user_id)
        return ""
