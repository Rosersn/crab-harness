"""Per-user MCP tool loading.

Loads user-level MCP server configurations from PG and creates MCP tool
instances, replacing the global get_cached_mcp_tools() for multi-tenant mode.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from crab_platform.db.repos.mcp_config_repo import McpConfigRepo

logger = logging.getLogger(__name__)

# Overall timeout for connecting to user MCP servers and loading tools.
_MCP_LOAD_TIMEOUT_SECONDS = 30


async def load_user_mcp_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[BaseTool]:
    """Load MCP tools for a user from their PG-stored MCP configs.

    This loads the user's personally configured MCP servers (HTTP/SSE only),
    creates connections, and returns the tool list.  Platform-level MCP tools
    (from extensions_config.json) are loaded separately via the existing
    get_cached_mcp_tools() path.

    Returns an empty list if the user has no MCP configs or if loading fails.
    """
    repo = McpConfigRepo(db)
    configs = await repo.list_for_user(user_id)

    if not configs:
        return []

    enabled_configs = [c for c in configs if c.enabled]
    if not enabled_configs:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters not installed, skipping user MCP tools")
        return []

    # Build server config dict from PG records
    servers_config: dict[str, dict] = {}
    for cfg in enabled_configs:
        config_data = cfg.config if isinstance(cfg.config, dict) else {}
        server_entry: dict = {
            "transport": cfg.transport_type,  # "http" or "sse"
            "url": config_data.get("url", ""),
        }
        if config_data.get("headers"):
            server_entry["headers"] = config_data["headers"]
        if config_data.get("timeout"):
            server_entry["timeout"] = config_data["timeout"]

        servers_config[cfg.server_name] = server_entry

    if not servers_config:
        return []

    try:
        async with asyncio.timeout(_MCP_LOAD_TIMEOUT_SECONDS):
            async with MultiServerMCPClient(servers_config) as client:
                tools = await client.get_tools()
                logger.info("Loaded %d user MCP tools for user %s", len(tools), user_id)
                return tools
    except TimeoutError:
        logger.warning("Timed out loading user MCP tools for %s after %ds", user_id, _MCP_LOAD_TIMEOUT_SECONDS)
        return []
    except Exception:
        logger.exception("Failed to load user MCP tools for %s", user_id)
        return []
