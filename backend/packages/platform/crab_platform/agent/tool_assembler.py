"""Per-user tool assembly.

Combines platform tools, user MCP tools, built-in tools, and subagent tools
into the full tool set for a specific user request.  Replaces the global
get_available_tools() for multi-tenant mode.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def assemble_user_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    model_name: str | None = None,
    groups: list[str] | None = None,
    subagent_enabled: bool = False,
) -> list[BaseTool]:
    """Assemble the full tool set for a user request.

    Combines:
    1. Platform tools (from config.yaml, filtered by groups)
    2. Platform MCP tools (from extensions_config.json, shared)
    3. User MCP tools (from PG, per-user)
    4. Built-in tools (present_files, ask_clarification, view_image)
    5. Subagent tool (if enabled)
    6. ACP tools (if configured)

    This wraps the harness-level get_available_tools() and extends it with
    per-user MCP tools from PG.
    """
    from deerflow.tools import get_available_tools

    # 1-2 + 4-6: Platform tools + shared MCP + built-ins + subagent + ACP
    # get_available_tools() handles all of these via the global config
    platform_tools = get_available_tools(
        groups=groups,
        include_mcp=True,
        model_name=model_name,
        subagent_enabled=subagent_enabled,
    )

    # 3: User-level MCP tools (from PG)
    from crab_platform.agent.mcp_loader import load_user_mcp_tools

    user_mcp_tools = await load_user_mcp_tools(db, user_id)

    if user_mcp_tools:
        # Deduplicate by tool name — platform tools take precedence over user tools with same name
        platform_names = {t.name for t in platform_tools}
        new_tools = [t for t in user_mcp_tools if t.name not in platform_names]
        logger.info(
            "Assembled %d platform + %d user MCP tools for user %s",
            len(platform_tools), len(new_tools), user_id,
        )
        return platform_tools + new_tools

    return platform_tools
