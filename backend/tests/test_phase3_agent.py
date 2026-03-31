"""Phase 3 tests: per-user agent construction (memory, MCP, skills, tool assembly, factory)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_user_id():
    return uuid.uuid4()


def _fake_tenant_id():
    return uuid.uuid4()


def _fake_db():
    return AsyncMock()


# ---------------------------------------------------------------------------
# Memory Injector
# ---------------------------------------------------------------------------


class TestMemoryInjector:
    """Tests for per-user memory injection from PG."""

    def test_load_user_memory_returns_empty_when_none(self):
        from crab_platform.agent.memory_injector import load_user_memory

        db = _fake_db()
        repo_mock = MagicMock()
        repo_mock.load = AsyncMock(return_value=None)

        with patch("crab_platform.agent.memory_injector.MemoryRepo", return_value=repo_mock):
            data = asyncio.run(load_user_memory(db, _fake_user_id()))

        assert data["version"] == "1.0"
        assert data["facts"] == []

    def test_load_user_memory_returns_stored_data(self):
        from crab_platform.agent.memory_injector import load_user_memory

        db = _fake_db()
        stored = {"version": "1.0", "facts": [{"id": "f1", "content": "likes python"}]}
        repo_mock = MagicMock()
        repo_mock.load = AsyncMock(return_value=stored)

        with patch("crab_platform.agent.memory_injector.MemoryRepo", return_value=repo_mock):
            data = asyncio.run(load_user_memory(db, _fake_user_id()))

        assert data == stored

    def test_format_user_memory_context_returns_empty_when_disabled(self):
        from crab_platform.agent.memory_injector import format_user_memory_context

        db = _fake_db()
        config = MagicMock()
        config.enabled = False
        config.injection_enabled = True

        with patch("crab_platform.agent.memory_injector.MemoryRepo"):
            with patch("crab.config.memory_config.get_memory_config", return_value=config):
                result = asyncio.run(format_user_memory_context(db, _fake_user_id()))

        assert result == ""

    def test_format_user_memory_context_returns_xml_tags(self):
        from crab_platform.agent.memory_injector import format_user_memory_context

        db = _fake_db()
        memory_data = {
            "version": "1.0",
            "user": {
                "workContext": {"summary": "Working on AI project", "updatedAt": "2025-01-01"},
                "personalContext": {"summary": "", "updatedAt": ""},
                "topOfMind": {"summary": "", "updatedAt": ""},
            },
            "history": {
                "recentMonths": {"summary": "", "updatedAt": ""},
                "earlierContext": {"summary": "", "updatedAt": ""},
                "longTermBackground": {"summary": "", "updatedAt": ""},
            },
            "facts": [],
        }

        config = MagicMock()
        config.enabled = True
        config.injection_enabled = True
        config.max_injection_tokens = 2000

        repo_mock = MagicMock()
        repo_mock.load = AsyncMock(return_value=memory_data)

        with (
            patch("crab_platform.agent.memory_injector.MemoryRepo", return_value=repo_mock),
            patch("crab.config.memory_config.get_memory_config", return_value=config),
        ):
            result = asyncio.run(format_user_memory_context(db, _fake_user_id()))

        assert "<memory>" in result
        assert "</memory>" in result
        assert "Working on AI project" in result


# ---------------------------------------------------------------------------
# Skill Loader
# ---------------------------------------------------------------------------


class TestSkillLoader:
    """Tests for per-user skill loading with PG overrides."""

    def _make_skill(self, name, enabled=True, category="public"):
        skill = MagicMock()
        skill.name = name
        skill.enabled = enabled
        skill.category = category
        return skill

    def test_load_user_skills_applies_overrides(self):
        from crab_platform.agent.skill_loader import load_user_skills

        db = _fake_db()
        user_id = _fake_user_id()

        # Platform skills: A (enabled), B (enabled), C (disabled)
        s_a = self._make_skill("skill-a", enabled=True)
        s_b = self._make_skill("skill-b", enabled=True)
        s_c = self._make_skill("skill-c", enabled=False)

        # User override: disable A, enable C
        override_a = MagicMock()
        override_a.skill_name = "skill-a"
        override_a.enabled = False
        override_c = MagicMock()
        override_c.skill_name = "skill-c"
        override_c.enabled = True

        repo_mock = MagicMock()
        repo_mock.list_for_user = AsyncMock(return_value=[override_a, override_c])

        with (
            patch("crab.skills.load_skills", return_value=[s_a, s_b, s_c]),
            patch("crab_platform.agent.skill_loader.SkillConfigRepo", return_value=repo_mock),
        ):
            result = asyncio.run(load_user_skills(db, user_id, enabled_only=True))

        names = [s.name for s in result]
        assert "skill-a" not in names  # disabled by user
        assert "skill-b" in names  # platform default enabled
        assert "skill-c" in names  # enabled by user override

    def test_get_user_enabled_skill_names(self):
        from crab_platform.agent.skill_loader import get_user_enabled_skill_names

        db = _fake_db()
        user_id = _fake_user_id()

        s1 = self._make_skill("web-search", enabled=True)
        s2 = self._make_skill("code-review", enabled=True)

        repo_mock = MagicMock()
        repo_mock.list_for_user = AsyncMock(return_value=[])

        with (
            patch("crab.skills.load_skills", return_value=[s1, s2]),
            patch("crab_platform.agent.skill_loader.SkillConfigRepo", return_value=repo_mock),
        ):
            names = asyncio.run(get_user_enabled_skill_names(db, user_id))

        assert names == {"web-search", "code-review"}


# ---------------------------------------------------------------------------
# MCP Loader
# ---------------------------------------------------------------------------


class TestMcpLoader:
    """Tests for per-user MCP tool loading."""

    def test_returns_empty_when_no_configs(self):
        from crab_platform.agent.mcp_loader import load_user_mcp_tools

        db = _fake_db()
        repo_mock = MagicMock()
        repo_mock.list_for_user = AsyncMock(return_value=[])

        with patch("crab_platform.agent.mcp_loader.McpConfigRepo", return_value=repo_mock):
            tools = asyncio.run(load_user_mcp_tools(db, _fake_user_id()))

        assert tools == []

    def test_returns_empty_when_all_disabled(self):
        from crab_platform.agent.mcp_loader import load_user_mcp_tools

        db = _fake_db()
        cfg = MagicMock()
        cfg.enabled = False
        cfg.server_name = "test-server"
        cfg.transport_type = "http"
        cfg.config = {"url": "https://example.com"}

        repo_mock = MagicMock()
        repo_mock.list_for_user = AsyncMock(return_value=[cfg])

        with patch("crab_platform.agent.mcp_loader.McpConfigRepo", return_value=repo_mock):
            tools = asyncio.run(load_user_mcp_tools(db, _fake_user_id()))

        assert tools == []


# ---------------------------------------------------------------------------
# Tool Assembler
# ---------------------------------------------------------------------------


class TestToolAssembler:
    """Tests for per-user tool assembly."""

    def test_includes_platform_tools(self):
        from crab_platform.agent.tool_assembler import assemble_user_tools

        db = _fake_db()
        user_id = _fake_user_id()

        tool1 = MagicMock()
        tool1.name = "platform-tool"
        platform_tools = [tool1]

        with (
            patch("crab.tools.get_available_tools", return_value=platform_tools),
            patch("crab_platform.agent.mcp_loader.load_user_mcp_tools", new_callable=AsyncMock, return_value=[]),
        ):
            tools = asyncio.run(assemble_user_tools(db, user_id))

        assert len(tools) == 1
        assert tools[0].name == "platform-tool"

    def test_appends_user_mcp_tools(self):
        from crab_platform.agent.tool_assembler import assemble_user_tools

        db = _fake_db()
        user_id = _fake_user_id()

        platform_tool = MagicMock()
        platform_tool.name = "platform-tool"
        user_tool = MagicMock()
        user_tool.name = "user-mcp-tool"

        with (
            patch("crab.tools.get_available_tools", return_value=[platform_tool]),
            patch("crab_platform.agent.mcp_loader.load_user_mcp_tools", new_callable=AsyncMock, return_value=[user_tool]),
        ):
            tools = asyncio.run(assemble_user_tools(db, user_id))

        names = [t.name for t in tools]
        assert "platform-tool" in names
        assert "user-mcp-tool" in names

    def test_deduplicates_by_name(self):
        from crab_platform.agent.tool_assembler import assemble_user_tools

        db = _fake_db()
        user_id = _fake_user_id()

        platform_tool = MagicMock()
        platform_tool.name = "shared-name"
        user_tool = MagicMock()
        user_tool.name = "shared-name"  # same name as platform

        with (
            patch("crab.tools.get_available_tools", return_value=[platform_tool]),
            patch("crab_platform.agent.mcp_loader.load_user_mcp_tools", new_callable=AsyncMock, return_value=[user_tool]),
        ):
            tools = asyncio.run(assemble_user_tools(db, user_id))

        # Should not duplicate — user tool with same name is skipped
        assert len(tools) == 1
        assert tools[0] is platform_tool


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestTenantAgentFactory:
    """Tests for make_tenant_agent factory."""

    def test_factory_calls_create_agent(self):
        from crab_platform.agent.factory import make_tenant_agent
        from crab_platform.context import RequestContext

        db = _fake_db()
        ctx = RequestContext(
            user_id=_fake_user_id(),
            tenant_id=_fake_tenant_id(),
            thread_id=uuid.uuid4(),
            model_name=None,
            thinking_enabled=False,
        )

        mock_model = MagicMock()
        mock_tools = [MagicMock()]
        mock_middlewares = [MagicMock()]
        mock_agent = MagicMock()

        with (
            patch("crab_platform.agent.factory._resolve_model_name", return_value="test-model"),
            patch("crab_platform.agent.factory.load_agent_config", return_value=None),
            patch("crab_platform.agent.factory.get_app_config") as mock_config,
            patch("crab_platform.agent.factory.create_chat_model", return_value=mock_model),
            patch("crab_platform.agent.factory.assemble_user_tools", new_callable=AsyncMock, return_value=mock_tools),
            patch("crab_platform.agent.factory._build_middlewares", return_value=mock_middlewares),
            patch("crab_platform.agent.factory._build_tenant_prompt", new_callable=AsyncMock, return_value="system prompt"),
            patch("crab_platform.agent.factory.create_agent", return_value=mock_agent) as create_mock,
        ):
            # Setup model config
            model_cfg = MagicMock()
            model_cfg.supports_thinking = False
            mock_config.return_value.get_model_config.return_value = model_cfg

            agent, runnable_config = asyncio.run(make_tenant_agent(ctx, db))

        assert agent is mock_agent
        create_mock.assert_called_once()
        call_kwargs = create_mock.call_args
        assert call_kwargs.kwargs["model"] is mock_model
        assert call_kwargs.kwargs["tools"] is mock_tools
        assert call_kwargs.kwargs["system_prompt"] == "system prompt"

    def test_factory_uses_ctx_model_name(self):
        from crab_platform.agent.factory import make_tenant_agent
        from crab_platform.context import RequestContext

        db = _fake_db()
        ctx = RequestContext(
            user_id=_fake_user_id(),
            tenant_id=_fake_tenant_id(),
            model_name="custom-model",
            thinking_enabled=True,
        )

        model_cfg = MagicMock()
        model_cfg.supports_thinking = True

        with (
            patch("crab_platform.agent.factory._resolve_model_name", return_value="default-model"),
            patch("crab_platform.agent.factory.load_agent_config", return_value=None),
            patch("crab_platform.agent.factory.get_app_config") as mock_config,
            patch("crab_platform.agent.factory.create_chat_model", return_value=MagicMock()) as model_mock,
            patch("crab_platform.agent.factory.assemble_user_tools", new_callable=AsyncMock, return_value=[]),
            patch("crab_platform.agent.factory._build_middlewares", return_value=[]),
            patch("crab_platform.agent.factory._build_tenant_prompt", new_callable=AsyncMock, return_value="prompt"),
            patch("crab_platform.agent.factory.create_agent", return_value=MagicMock()),
        ):
            mock_config.return_value.get_model_config.return_value = model_cfg
            asyncio.run(make_tenant_agent(ctx, db))

        # create_chat_model should be called with the ctx model_name
        model_mock.assert_called_once()
        assert model_mock.call_args.kwargs["name"] == "custom-model"

    def test_factory_disables_thinking_if_model_unsupported(self):
        from crab_platform.agent.factory import make_tenant_agent
        from crab_platform.context import RequestContext

        db = _fake_db()
        ctx = RequestContext(
            user_id=_fake_user_id(),
            tenant_id=_fake_tenant_id(),
            thinking_enabled=True,  # requested
        )

        model_cfg = MagicMock()
        model_cfg.supports_thinking = False  # not supported

        with (
            patch("crab_platform.agent.factory._resolve_model_name", return_value="basic-model"),
            patch("crab_platform.agent.factory.load_agent_config", return_value=None),
            patch("crab_platform.agent.factory.get_app_config") as mock_config,
            patch("crab_platform.agent.factory.create_chat_model", return_value=MagicMock()) as model_mock,
            patch("crab_platform.agent.factory.assemble_user_tools", new_callable=AsyncMock, return_value=[]),
            patch("crab_platform.agent.factory._build_middlewares", return_value=[]),
            patch("crab_platform.agent.factory._build_tenant_prompt", new_callable=AsyncMock, return_value="prompt"),
            patch("crab_platform.agent.factory.create_agent", return_value=MagicMock()),
        ):
            mock_config.return_value.get_model_config.return_value = model_cfg
            asyncio.run(make_tenant_agent(ctx, db))

        # thinking_enabled should be overridden to False
        assert model_mock.call_args.kwargs["thinking_enabled"] is False
