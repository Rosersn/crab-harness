"""Tenant-level Agent factory.

Wraps the harness make_lead_agent() to inject per-user data:
  - Per-user tools (user MCP + platform tools)
  - Per-user memory (from PG, injected into system prompt)
  - Per-user skills (platform + user overrides)

The harness-level create_agent(), _build_middlewares(), and create_chat_model()
are reused as-is.  Only tool assembly, memory injection, and skill loading are
replaced with per-user versions.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.agent.memory_injector import format_user_memory_context
from crab_platform.agent.tool_assembler import assemble_user_tools
from crab_platform.context import RequestContext

# Reuse harness internals
from deerflow.agents.lead_agent.agent import _build_middlewares, _resolve_model_name
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import load_agent_config
from deerflow.config.app_config import get_app_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)


async def make_tenant_agent(
    ctx: RequestContext,
    db: AsyncSession,
    *,
    checkpointer: Any | None = None,
    recursion_limit: int = 100,
):
    """Create a per-user Agent with tenant-scoped tools, memory, and skills.

    This is the multi-tenant replacement for ``make_lead_agent(config)``.
    It reuses the harness middleware chain and model factory, but replaces:
    - Tool assembly → per-user (platform + user MCP)
    - Memory injection → per-user (from PG)
    - Skill loading → per-user (platform + user overrides)

    Args:
        ctx: RequestContext with user_id, tenant_id, model_name, etc.
        db: AsyncSession for PG access.
        checkpointer: Optional LangGraph checkpointer for state persistence.
        recursion_limit: Max recursion depth for the agent graph.

    Returns:
        A tuple of (compiled_agent, runnable_config).
    """
    config = RunnableConfig(configurable=ctx.to_runnable_configurable())

    thinking_enabled = ctx.thinking_enabled
    reasoning_effort = ctx.reasoning_effort
    subagent_enabled = ctx.subagent_enabled
    max_concurrent_subagents = ctx.max_concurrent_subagents
    agent_name = ctx.agent_name

    if agent_name:
        raise ValueError("Custom agents are not supported in cloud mode.")

    # Load agent-specific config (tool groups, model override, etc.)
    agent_config = load_agent_config(agent_name) if agent_name else None
    agent_model_name = (
        agent_config.model
        if agent_config and agent_config.model
        else _resolve_model_name()
    )

    # Model resolution: request override → agent config → global default
    model_name = ctx.model_name or agent_model_name

    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name) if model_name else None
    if model_config is None:
        raise ValueError(
            "No chat model could be resolved. Please configure at least one "
            "model in config.yaml or provide a valid model_name in the request."
        )

    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(
            "Thinking mode requested but model '%s' does not support it; disabling.",
            model_name,
        )
        thinking_enabled = False

    logger.info(
        "make_tenant_agent(user=%s, model=%s, thinking=%s, subagent=%s, agent=%s)",
        ctx.user_id, model_name, thinking_enabled, subagent_enabled, agent_name,
    )

    # 1. Create the chat model (reuse harness factory)
    model = create_chat_model(
        name=model_name,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )

    # 2. Assemble per-user tools
    tools = await assemble_user_tools(
        db=db,
        user_id=ctx.user_id,
        model_name=model_name,
        groups=agent_config.tool_groups if agent_config else None,
        subagent_enabled=subagent_enabled,
    )

    # 3. Build middleware chain (reuse harness chain — middleware reads
    #    user_id/tenant_id from config["configurable"] when needed)
    middlewares = _build_middlewares(config, model_name=model_name, agent_name=agent_name)

    # 4. Build per-user system prompt
    system_prompt = await _build_tenant_prompt(
        ctx=ctx,
        db=db,
        subagent_enabled=subagent_enabled,
        max_concurrent_subagents=max_concurrent_subagents,
        agent_name=agent_name,
    )

    # 5. Create the agent
    agent_kwargs: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "middleware": middlewares,
        "system_prompt": system_prompt,
        "state_schema": ThreadState,
    }
    if checkpointer is not None:
        agent_kwargs["checkpointer"] = checkpointer

    agent = create_agent(**agent_kwargs)

    # Return both agent and config so caller can pass to astream()
    runnable_config = RunnableConfig(
        configurable=config.get("configurable", {}),
        recursion_limit=recursion_limit,
    )
    return agent, runnable_config


async def _build_tenant_prompt(
    ctx: RequestContext,
    db: AsyncSession,
    subagent_enabled: bool = False,
    max_concurrent_subagents: int = 3,
    agent_name: str | None = None,
) -> str:
    """Build the system prompt with per-user memory and skills.

    Delegates to the harness ``apply_prompt_template()`` with per-user data:
    - memory_context: loaded from PG (instead of file-based)
    - available_skills: filtered by user's PG overrides
    - extra_skills: custom user skills from BOS (not on filesystem)
    - default_agent_name: "Crab Harness" (instead of "DeerFlow 2.0")
    """
    # Per-user memory from PG
    memory_context = await format_user_memory_context(
        db=db,
        user_id=ctx.user_id,
        agent_name=agent_name,
    )

    # Per-user skills (includes both platform + custom BOS skills)
    from crab_platform.agent.skill_loader import load_user_skills

    user_skills = await load_user_skills(db, ctx.user_id, enabled_only=True)
    user_skill_names = {s.name for s in user_skills}

    # Separate custom skills (category='custom') to pass as extra_skills
    # so get_skills_prompt_section() can include them alongside platform skills.
    extra_skills = [s for s in user_skills if s.category == "custom"]

    # Delegate to harness prompt assembly — all subagent/skills/deferred-tools/ACP
    # logic stays in one place, no duplication.
    return apply_prompt_template(
        subagent_enabled=subagent_enabled,
        max_concurrent_subagents=max_concurrent_subagents,
        agent_name=agent_name,
        available_skills=user_skill_names,
        extra_skills=extra_skills if extra_skills else None,
        memory_context=memory_context,
        default_agent_name="Crab Harness",
    )
