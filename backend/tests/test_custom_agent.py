"""Tests for custom agent support."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from crab_platform.auth.interface import AuthenticatedUser
from fastapi.testclient import TestClient

from app.gateway.deps import get_current_user

_FAKE_USER = AuthenticatedUser(
    user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="test@example.com", role="member",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from crab.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(base_dir: Path, name: str, config: dict, soul: str = "You are helpful.") -> None:
    """Write an agent directory with config.yaml and SOUL.md."""
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class – agent path methods
# ===========================================================================


class TestPaths:
    def test_agents_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agents_dir == tmp_path / "agents"

    def test_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_dir("code-reviewer") == tmp_path / "agents" / "code-reviewer"

    def test_agent_memory_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_memory_file("code-reviewer") == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_user_md_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_md_file == tmp_path / "USER.md"

    def test_paths_are_different_from_global(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.memory_file != paths.agent_memory_file("my-agent")
        assert paths.memory_file == tmp_path / "memory.json"
        assert paths.agent_memory_file("my-agent") == tmp_path / "agents" / "my-agent" / "memory.json"


# ===========================================================================
# 2. AgentConfig – Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from crab.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from crab.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            tool_groups=["file:read", "bash"],
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.tool_groups == ["file:read", "bash"]

    def test_config_from_dict(self):
        from crab.config.agents_config import AgentConfig

        data = {"name": "test-agent", "description": "A test", "model": "gpt-4"}
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "agents" / "broken-agent").mkdir(parents=True)

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_legacy_prompt_file_field_ignored(self, tmp_path):
        """Unknown fields like the old prompt_file should be silently ignored."""
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: legacy-agent\nprompt_file: system.md\n")
        (agent_dir / "SOUL.md").write_text("Soul content")

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import load_agent_config

            cfg = load_agent_config("legacy-agent")

        assert cfg.name == "legacy-agent"


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("crab.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from crab.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


class TestMemoryFilePath:
    def test_global_memory_path(self, tmp_path):
        """None agent_name should return global memory file."""
        from crab.agents.memory.storage import FileMemoryStorage
        from crab.config.memory_config import MemoryConfig

        with (
            patch("crab.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("crab.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path(None)
        assert path == tmp_path / "memory.json"

    def test_agent_memory_path(self, tmp_path):
        """Providing agent_name should return per-agent memory file."""
        from crab.agents.memory.storage import FileMemoryStorage
        from crab.config.memory_config import MemoryConfig

        with (
            patch("crab.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("crab.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path("code-reviewer")
        assert path == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_different_paths_for_different_agents(self, tmp_path):
        from crab.agents.memory.storage import FileMemoryStorage
        from crab.config.memory_config import MemoryConfig

        with (
            patch("crab.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("crab.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path_global = storage._get_memory_file_path(None)
            path_a = storage._get_memory_file_path("agent-a")
            path_b = storage._get_memory_file_path("agent-b")

        assert path_global != path_a
        assert path_global != path_b
        assert path_a != path_b


# ===========================================================================
# 8. Gateway API – Agents endpoints
# ===========================================================================


def _make_test_app():
    """Create a FastAPI app with the agents router."""
    from fastapi import FastAPI

    from app.gateway.routers.agents import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return app


@pytest.fixture()
def agent_client():
    """TestClient with agents router."""
    app = _make_test_app()
    with TestClient(app) as client:
        yield client


class TestAgentsAPI:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("get", "/api/agents", None),
            ("get", "/api/agents/check?name=test-agent", None),
            ("get", "/api/agents/test-agent", None),
            (
                "post",
                "/api/agents",
                {
                    "name": "code-reviewer",
                    "description": "Reviews code",
                    "soul": "You are a code reviewer.",
                },
            ),
            ("put", "/api/agents/test-agent", {"soul": "updated"}),
            ("delete", "/api/agents/test-agent", None),
        ],
    )
    def test_cloud_mode_disables_custom_agent_endpoints(
        self,
        agent_client,
        method: str,
        path: str,
        payload: dict | None,
    ):
        if payload is None:
            response = getattr(agent_client, method)(path)
        else:
            response = getattr(agent_client, method)(path, json=payload)
        assert response.status_code == 410
        assert "not supported in cloud mode" in response.json()["detail"]


# ===========================================================================
# 9. Gateway API – User Profile endpoints
# ===========================================================================


class TestUserProfileAPI:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("get", "/api/user-profile", None),
            ("put", "/api/user-profile", {"content": "# User Profile\n\nI am a developer."}),
        ],
    )
    def test_cloud_mode_disables_user_profile_endpoints(
        self,
        agent_client,
        method: str,
        path: str,
        payload: dict | None,
    ):
        if payload is None:
            response = getattr(agent_client, method)(path)
        else:
            response = getattr(agent_client, method)(path, json=payload)
        assert response.status_code == 410
        assert "not supported in cloud mode" in response.json()["detail"]
