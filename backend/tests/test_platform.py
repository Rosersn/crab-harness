"""Tests for crab_platform auth, repos, context, and gateway deps/routes.

These are unit tests that mock the database layer to test business logic
without requiring PostgreSQL or Redis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Password hashing ────────────────────────────────────────────────────


class TestPasswordHashing:
    def test_hash_and_verify(self):
        from crab_platform.auth.password import hash_password, verify_password

        plain = "my-secret-password"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_wrong_password(self):
        from crab_platform.auth.password import hash_password, verify_password

        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_different_hashes(self):
        from crab_platform.auth.password import hash_password

        h1 = hash_password("same")
        h2 = hash_password("same")
        # bcrypt salts differ
        assert h1 != h2


# ── AuthenticatedUser ────────────────────────────────────────────────────


class TestAuthenticatedUser:
    def test_frozen(self):
        from crab_platform.auth.interface import AuthenticatedUser

        u = AuthenticatedUser(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            email="test@example.com",
            role="member",
        )
        with pytest.raises(AttributeError):
            u.email = "changed@example.com"


# ── JWT tokens ───────────────────────────────────────────────────────────


class TestJWTTokens:
    """Test JWT creation and verification without a real DB."""

    def _make_user(self):
        """Create a minimal mock User object for token creation."""
        user = MagicMock()
        user.id = uuid.uuid4()
        user.tenant_id = uuid.uuid4()
        user.email = "test@example.com"
        user.role = "member"
        user.password_hash = "hash"
        return user

    def _make_provider(self):
        from crab_platform.auth.jwt import JWTAuthProvider

        mock_session = AsyncMock()
        with patch("crab_platform.auth.jwt.get_platform_config") as mock_config:
            cfg = MagicMock()
            cfg.jwt_secret = "test-secret-key"
            cfg.jwt_algorithm = "HS256"
            cfg.jwt_access_token_expire_minutes = 60
            cfg.jwt_refresh_token_expire_days = 30
            mock_config.return_value = cfg
            provider = JWTAuthProvider(mock_session)
        return provider

    def test_create_access_token(self):
        provider = self._make_provider()
        user = self._make_user()
        token = provider._create_access_token(user)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_create_refresh_token(self):
        provider = self._make_provider()
        user = self._make_user()
        token = provider._create_refresh_token(user)
        assert isinstance(token, str)

    @pytest.mark.asyncio
    async def test_authenticate_valid_token(self):
        provider = self._make_provider()
        user = self._make_user()
        token = provider._create_access_token(user)

        result = await provider.authenticate(token)
        assert result is not None
        assert result.user_id == user.id
        assert result.tenant_id == user.tenant_id
        assert result.email == user.email
        assert result.role == user.role

    @pytest.mark.asyncio
    async def test_authenticate_invalid_token(self):
        provider = self._make_provider()
        result = await provider.authenticate("invalid-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_refresh_token_rejected(self):
        """Access endpoint should reject refresh tokens."""
        provider = self._make_provider()
        user = self._make_user()
        refresh = provider._create_refresh_token(user)
        result = await provider.authenticate(refresh)
        assert result is None


# ── RequestContext ────────────────────────────────────────────────────────


class TestRequestContext:
    def test_frozen(self):
        from crab_platform.context import RequestContext

        ctx = RequestContext()
        with pytest.raises(AttributeError):
            ctx.tenant_id = uuid.uuid4()

    def test_to_runnable_configurable(self):
        from crab_platform.context import RequestContext

        tid = uuid.uuid4()
        uid = uuid.uuid4()
        thread_id = uuid.uuid4()
        ctx = RequestContext(
            tenant_id=tid,
            user_id=uid,
            thread_id=thread_id,
            model_name="gpt-4o",
            thinking_enabled=True,
            is_plan_mode=False,
        )
        cfg = ctx.to_runnable_configurable()
        assert cfg["thread_id"] == str(thread_id)
        assert cfg["user_id"] == str(uid)
        assert cfg["tenant_id"] == str(tid)
        assert cfg["model_name"] == "gpt-4o"
        assert cfg["thinking_enabled"] is True
        assert cfg["is_plan_mode"] is False

    def test_optional_model_not_in_configurable(self):
        from crab_platform.context import RequestContext

        ctx = RequestContext()
        cfg = ctx.to_runnable_configurable()
        assert "model_name" not in cfg


# ── PlatformConfig ───────────────────────────────────────────────────────


class TestPlatformConfig:
    def test_defaults(self):
        from crab_platform.config.platform_config import PlatformConfig

        cfg = PlatformConfig()
        assert "postgresql" in cfg.database_url
        assert "redis" in cfg.redis_url
        assert cfg.jwt_algorithm == "HS256"
        assert cfg.jwt_access_token_expire_minutes == 60


# ── Auth router schemas ──────────────────────────────────────────────────


class TestAuthRouterSchemas:
    def test_token_response(self):
        from app.gateway.routers.auth import TokenResponse

        resp = TokenResponse(access_token="a", refresh_token="r")
        assert resp.token_type == "bearer"

    def test_user_response(self):
        from app.gateway.routers.auth import UserResponse

        resp = UserResponse(
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            email="test@example.com",
            role="admin",
        )
        assert resp.role == "admin"


# ── Middleware ────────────────────────────────────────────────────────────


class TestAuthEnforcementMiddleware:
    def test_public_paths_identified(self):
        from app.gateway.middleware import PUBLIC_PATHS, PUBLIC_PREFIXES

        assert "/health" in PUBLIC_PATHS
        assert "/api/auth/" in PUBLIC_PREFIXES


class TestGatewayApp:
    def test_cors_exposes_content_location(self):
        from app.gateway.app import create_app

        app = create_app()
        cors_middleware = next(
            middleware
            for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )

        assert "Content-Location" in cors_middleware.kwargs["expose_headers"]

    def test_join_stream_route_supports_get_for_sdk_reconnect(self):
        from app.gateway.app import create_app

        app = create_app()
        join_stream_route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/api/langgraph/threads/{thread_id}/runs/{run_id}/stream"
        )

        assert "GET" in join_stream_route.methods
        assert "POST" in join_stream_route.methods


# ── LangGraph compat router schemas ─────────────────────────────────────


class TestLangGraphCompatSchemas:
    def test_thread_response(self):
        from app.gateway.routers.langgraph_compat import ThreadResponse

        resp = ThreadResponse(
            thread_id=str(uuid.uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        assert resp.values == {}

    def test_run_stream_request_defaults(self):
        from app.gateway.routers.langgraph_compat import RunStreamRequest

        req = RunStreamRequest()
        assert req.assistant_id == "lead_agent"
        assert req.stream_resumable is False
        assert req.on_disconnect is None

    def test_thread_search_request_defaults(self):
        from app.gateway.routers.langgraph_compat import ThreadSearchRequest

        req = ThreadSearchRequest()
        assert req.limit == 50
        assert req.offset == 0
        assert req.sort_by == "updated_at"


class TestLangGraphCompatHelpers:
    def test_internal_stream_message_detects_title_middleware_node(self):
        from app.gateway.routers.langgraph_compat import _is_internal_stream_message

        assert _is_internal_stream_message({"langgraph_node": "TitleMiddleware.after_model"}) is True

    def test_internal_stream_message_ignores_primary_model_node(self):
        from app.gateway.routers.langgraph_compat import _is_internal_stream_message

        assert _is_internal_stream_message({"langgraph_node": "model"}) is False
        assert _is_internal_stream_message({"langgraph_node": "tools"}) is False
        assert _is_internal_stream_message({"langgraph_node": "agent"}) is False
        assert _is_internal_stream_message({}) is False

    def test_internal_stream_message_detects_other_middleware_hooks(self):
        from app.gateway.routers.langgraph_compat import _is_internal_stream_message

        assert _is_internal_stream_message({"langgraph_node": "LoopDetectionMiddleware.after_model"}) is True
        assert _is_internal_stream_message({"langgraph_node": "MemoryMiddleware.before_model"}) is True

    def test_filter_serialized_messages_by_id_removes_internal_ids(self):
        from app.gateway.routers.langgraph_compat import _filter_serialized_messages_by_id

        messages = [
            {"id": "user-1", "type": "human", "content": "hi"},
            {"id": "internal-1", "type": "ai", "content": "title"},
            {"id": "tool-1", "type": "tool", "content": "ok"},
        ]

        filtered = _filter_serialized_messages_by_id(messages, {"internal-1"})

        assert filtered == [
            {"id": "user-1", "type": "human", "content": "hi"},
            {"id": "tool-1", "type": "tool", "content": "ok"},
        ]

    def test_merge_serialized_messages_preserves_history(self):
        from app.gateway.routers.langgraph_compat import _merge_serialized_messages

        existing = [
            {"id": "h1", "type": "human", "content": "first"},
            {"id": "a1", "type": "ai", "content": "reply"},
        ]
        new = [
            {"id": "h2", "type": "human", "content": "second"},
            {"id": "a2", "type": "ai", "content": "next"},
        ]

        merged = _merge_serialized_messages(existing, new)

        assert [msg["id"] for msg in merged] == ["h1", "a1", "h2", "a2"]

    def test_merge_serialized_messages_replaces_by_id(self):
        from app.gateway.routers.langgraph_compat import _merge_serialized_messages

        existing = [{"id": "a1", "type": "ai", "content": "partial"}]
        new = [{"id": "a1", "type": "ai", "content": "complete"}]

        merged = _merge_serialized_messages(existing, new)

        assert merged == [{"id": "a1", "type": "ai", "content": "complete"}]

    def test_serialize_stream_message_flattens_chunk(self):
        from app.gateway.routers.langgraph_compat import _serialize_stream_message
        from langchain_core.messages import AIMessageChunk

        payload = _serialize_stream_message(AIMessageChunk(content="he", id="chunk-1"))

        assert payload["id"] == "chunk-1"
        assert payload["content"] == "he"
        assert payload["type"] == "AIMessageChunk"

    def test_serialize_langchain_message_preserves_reasoning_content(self):
        from app.gateway.routers.langgraph_compat import _serialize_langchain_message
        from langchain_core.messages import AIMessage

        payload = _serialize_langchain_message(
            AIMessage(
                content="final answer",
                id="ai-1",
                additional_kwargs={"reasoning_content": "step by step"},
            )
        )

        assert payload["type"] == "ai"
        assert payload["content"] == "final answer"
        assert payload["additional_kwargs"]["reasoning_content"] == "step by step"

    def test_run_content_location_matches_sdk_expectation(self):
        from app.gateway.routers.langgraph_compat import _run_content_location

        thread_id = uuid.uuid4()
        run_id = uuid.uuid4()

        assert _run_content_location(thread_id, run_id) == f"/threads/{thread_id}/runs/{run_id}"

    def test_has_active_run_stream_reflects_registry(self):
        from app.gateway.routers.langgraph_compat import (
            _cancel_events,
            _has_active_run_stream,
        )

        run_id = uuid.uuid4()

        assert _has_active_run_stream(run_id) is False
        _cancel_events[run_id] = object()  # type: ignore[assignment]
        try:
            assert _has_active_run_stream(run_id) is True
        finally:
            _cancel_events.pop(run_id, None)

    @pytest.mark.asyncio
    async def test_publish_run_live_event_tracks_latest_values(self):
        from app.gateway.routers.langgraph_compat import (
            _close_run_live_stream,
            _get_or_create_run_live_stream,
            _publish_run_live_event,
        )

        run_id = uuid.uuid4()
        state = _get_or_create_run_live_stream(run_id)

        await _publish_run_live_event(
            run_id,
            {"event": "values", "data": '{"messages":[{"id":"a1"}]}'},
        )

        assert state.latest_values_payload == '{"messages":[{"id":"a1"}]}'

        await _close_run_live_stream(run_id)

    @pytest.mark.asyncio
    async def test_get_thread_history_prefers_checkpointer(self):
        from app.gateway.routers.langgraph_compat import get_thread_history
        from crab_platform.auth.interface import AuthenticatedUser

        thread_id = uuid.uuid4()
        user = AuthenticatedUser(
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            email="test@example.com",
            role="member",
        )
        db = AsyncMock()
        thread = MagicMock(title="db-title")

        with (
            patch(
                "app.gateway.routers.langgraph_compat._get_owned_thread",
                new=AsyncMock(return_value=thread),
            ),
            patch(
                "app.gateway.routers.langgraph_compat._load_state_from_checkpointer",
                new=AsyncMock(
                    return_value={
                        "messages": [{"id": "m1", "type": "human", "content": "hello"}],
                        "title": "checkpoint-title",
                        "artifacts": ["report.md"],
                        "todos": [{"title": "todo"}],
                    }
                ),
            ),
        ):
            result = await get_thread_history(thread_id, None, user, db)

        assert result == [{
            "values": {
                "messages": [{"id": "m1", "type": "human", "content": "hello"}],
                "title": "checkpoint-title",
                "artifacts": ["report.md"],
                "todos": [{"title": "todo"}],
            },
            "next": [],
            "tasks": [],
        }]

    @pytest.mark.asyncio
    async def test_create_thread_honors_requested_thread_id(self):
        from app.gateway.routers.langgraph_compat import ThreadCreateRequest, create_thread
        from crab_platform.auth.interface import AuthenticatedUser

        requested_thread_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        created_at = datetime.now(UTC)
        thread = MagicMock(
            id=requested_thread_id,
            created_at=created_at,
            updated_at=created_at,
            metadata_={},
            title=None,
        )
        user = AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            email="test@example.com",
            role="member",
        )
        db = AsyncMock()
        repo = AsyncMock()
        repo.get.return_value = None
        repo.create.return_value = thread

        with patch("app.gateway.routers.langgraph_compat.ThreadRepo", return_value=repo):
            response = await create_thread(
                ThreadCreateRequest(thread_id=requested_thread_id, metadata={}),
                user,
                db,
            )

        repo.create.assert_awaited_once_with(
            id=requested_thread_id,
            tenant_id=tenant_id,
            user_id=user_id,
            metadata_={},
        )
        assert response.thread_id == str(requested_thread_id)

    @pytest.mark.asyncio
    async def test_create_thread_reuses_existing_requested_thread_id(self):
        from app.gateway.routers.langgraph_compat import ThreadCreateRequest, create_thread
        from crab_platform.auth.interface import AuthenticatedUser

        requested_thread_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        created_at = datetime.now(UTC)
        existing_thread = MagicMock(
            id=requested_thread_id,
            user_id=user_id,
            created_at=created_at,
            updated_at=created_at,
            metadata_={"source": "existing"},
            title=None,
        )
        user = AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            email="test@example.com",
            role="member",
        )
        db = AsyncMock()
        repo = AsyncMock()
        repo.get.return_value = existing_thread

        with patch("app.gateway.routers.langgraph_compat.ThreadRepo", return_value=repo):
            response = await create_thread(
                ThreadCreateRequest(thread_id=requested_thread_id, metadata={}),
                user,
                db,
            )

        repo.create.assert_not_called()
        assert response.thread_id == str(requested_thread_id)


# ── Layer boundary ───────────────────────────────────────────────────────


class TestLayerBoundary:
    """Ensure crab_platform never imports from app.*"""

    def test_platform_does_not_import_app(self):
        import importlib
        import pkgutil

        import crab_platform

        violations = []
        package_path = crab_platform.__path__

        for importer, modname, ispkg in pkgutil.walk_packages(package_path, prefix="crab_platform."):
            try:
                mod = importlib.import_module(modname)
            except ImportError:
                continue
            source_file = getattr(mod, "__file__", "")
            if source_file and source_file.endswith(".py"):
                with open(source_file) as f:
                    content = f.read()
                if "from app." in content or "import app." in content:
                    violations.append(modname)

        assert violations == [], f"Platform modules import from app: {violations}"
