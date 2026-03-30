"""Phase 2 tests: ObjectStorage, PGMemoryStorage, MCP/Skills per-user routers."""

import asyncio
import uuid
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ObjectStorage (LocalObjectStorage)
# ---------------------------------------------------------------------------


class TestLocalObjectStorage:
    """Tests for the local filesystem ObjectStorage implementation."""

    def _make_storage(self, tmp_path):
        from crab_platform.storage.local import LocalObjectStorage

        return LocalObjectStorage(root_dir=tmp_path)

    def test_put_and_get(self, tmp_path):
        storage = self._make_storage(tmp_path)
        asyncio.run(storage.put("a/b/file.txt", b"hello"))
        data = asyncio.run(storage.get("a/b/file.txt"))
        assert data == b"hello"

    def test_put_binary_io(self, tmp_path):
        storage = self._make_storage(tmp_path)
        asyncio.run(storage.put("data.bin", BytesIO(b"binary")))
        assert asyncio.run(storage.get("data.bin")) == b"binary"

    def test_get_missing_raises(self, tmp_path):
        storage = self._make_storage(tmp_path)
        with pytest.raises(FileNotFoundError):
            asyncio.run(storage.get("no/such/key"))

    def test_delete(self, tmp_path):
        storage = self._make_storage(tmp_path)
        asyncio.run(storage.put("to-delete.txt", b"bye"))
        asyncio.run(storage.delete("to-delete.txt"))
        with pytest.raises(FileNotFoundError):
            asyncio.run(storage.get("to-delete.txt"))

    def test_delete_missing_no_error(self, tmp_path):
        storage = self._make_storage(tmp_path)
        asyncio.run(storage.delete("nonexistent"))  # should not raise

    def test_exists(self, tmp_path):
        storage = self._make_storage(tmp_path)
        assert asyncio.run(storage.exists("x.txt")) is False
        asyncio.run(storage.put("x.txt", b"x"))
        assert asyncio.run(storage.exists("x.txt")) is True

    def test_list_keys(self, tmp_path):
        storage = self._make_storage(tmp_path)
        asyncio.run(storage.put("prefix/a.txt", b"a"))
        asyncio.run(storage.put("prefix/b.txt", b"b"))
        asyncio.run(storage.put("other/c.txt", b"c"))
        keys = asyncio.run(storage.list_keys("prefix"))
        assert "prefix/a.txt" in keys
        assert "prefix/b.txt" in keys
        assert "other/c.txt" not in keys

    def test_presigned_url_returns_path(self, tmp_path):
        storage = self._make_storage(tmp_path)
        url = asyncio.run(storage.generate_presigned_url("my/key.txt"))
        assert "my/key.txt" in url

    def test_traversal_rejected(self, tmp_path):
        storage = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            asyncio.run(storage.put("../../etc/passwd", b"bad"))


# ---------------------------------------------------------------------------
# PGMemoryStorage
# ---------------------------------------------------------------------------


class TestPGMemoryStorage:
    """Tests for the PostgreSQL-backed MemoryStorage implementation."""

    def _make_storage(self, load_return=None, save_return=None):
        from crab_platform.storage.pg_memory import PGMemoryStorage

        db = AsyncMock()
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        storage = PGMemoryStorage(db, user_id, tenant_id)

        # Mock the repo methods
        storage._repo.load = AsyncMock(return_value=load_return)
        storage._repo.save = AsyncMock(return_value=save_return or MagicMock())

        return storage

    def test_load_returns_empty_on_first_call(self):
        storage = self._make_storage(load_return=None)
        data = storage.reload()
        assert data["version"] == "1.0"
        assert data["facts"] == []

    def test_load_returns_cached(self):
        memory = {"version": "1.0", "facts": [{"id": "f1"}]}
        storage = self._make_storage(load_return=memory)
        # First call populates cache
        data1 = storage.reload()
        assert data1 == memory
        # Second call returns cached
        data2 = storage.load()
        assert data2 == memory
        # repo.load only called once (by reload)
        assert storage._repo.load.call_count == 1

    def test_reload_always_fetches(self):
        storage = self._make_storage(load_return={"version": "1.0", "facts": []})
        storage.reload()
        storage.reload()
        assert storage._repo.load.call_count == 2

    def test_save_updates_cache(self):
        storage = self._make_storage()
        new_data = {"version": "1.0", "facts": [{"id": "new"}]}
        result = storage.save(new_data)
        assert result is True
        # Cache is updated
        assert storage.load() == new_data
        storage._repo.save.assert_called_once()

    def test_save_returns_false_on_error(self):
        storage = self._make_storage()
        storage._repo.save = AsyncMock(side_effect=RuntimeError("db error"))
        result = storage.save({"version": "1.0", "facts": []})
        assert result is False

    def test_load_with_agent_name(self):
        agent_memory = {"version": "1.0", "facts": [{"id": "agent-fact"}]}
        storage = self._make_storage(load_return=agent_memory)
        data = storage.reload(agent_name="researcher")
        assert data == agent_memory

    def test_implements_memory_storage_abc(self):
        from deerflow.agents.memory.storage import MemoryStorage
        from crab_platform.storage.pg_memory import PGMemoryStorage

        assert issubclass(PGMemoryStorage, MemoryStorage)


# ---------------------------------------------------------------------------
# MCP Router (per-user CRUD)
# ---------------------------------------------------------------------------


class TestMcpRouter:
    """Tests for MCP per-user CRUD endpoints."""

    def _fake_user(self):
        user = MagicMock()
        user.user_id = uuid.uuid4()
        user.tenant_id = uuid.uuid4()
        user.email = "test@example.com"
        user.role = "member"
        return user

    def test_upsert_mcp_server_rejects_stdio(self):
        from app.gateway.routers.mcp import UserMcpServerRequest, upsert_user_mcp_server

        user = self._fake_user()
        db = AsyncMock()
        db.commit = AsyncMock()

        # McpConfigRepo.upsert raises ValueError for stdio
        repo_mock = MagicMock()
        repo_mock.upsert = AsyncMock(side_effect=ValueError("Only 'http' and 'sse' transports are allowed, got: stdio"))

        request = UserMcpServerRequest(enabled=True, transport_type="stdio", config={})

        with patch("app.gateway.routers.mcp.McpConfigRepo", return_value=repo_mock):
            try:
                asyncio.run(upsert_user_mcp_server("bad-server", request, user=user, db=db))
                assert False, "Expected HTTPException"
            except Exception as e:
                assert hasattr(e, "status_code") and e.status_code == 400
                assert "stdio" in str(e.detail)

    def test_upsert_mcp_server_success(self):
        from app.gateway.routers.mcp import UserMcpServerRequest, upsert_user_mcp_server

        user = self._fake_user()
        db = AsyncMock()
        db.commit = AsyncMock()

        record = MagicMock()
        record.server_name = "my-server"
        record.enabled = True
        record.transport_type = "http"
        record.config = {"url": "https://example.com"}

        repo_mock = MagicMock()
        repo_mock.upsert = AsyncMock(return_value=record)

        request = UserMcpServerRequest(enabled=True, transport_type="http", config={"url": "https://example.com"})

        with patch("app.gateway.routers.mcp.McpConfigRepo", return_value=repo_mock):
            result = asyncio.run(upsert_user_mcp_server("my-server", request, user=user, db=db))

        assert result.server_name == "my-server"
        assert result.transport_type == "http"
        db.commit.assert_called_once()

    def test_delete_mcp_server_not_found(self):
        from app.gateway.routers.mcp import delete_user_mcp_server

        user = self._fake_user()
        db = AsyncMock()

        repo_mock = MagicMock()
        repo_mock.delete = AsyncMock(return_value=False)

        with patch("app.gateway.routers.mcp.McpConfigRepo", return_value=repo_mock):
            try:
                asyncio.run(delete_user_mcp_server("nonexistent", user=user, db=db))
                assert False, "Expected HTTPException"
            except Exception as e:
                assert e.status_code == 404


# ---------------------------------------------------------------------------
# Skills Router (per-user CRUD)
# ---------------------------------------------------------------------------


class TestSkillsRouter:
    """Tests for Skills per-user CRUD endpoints."""

    def _fake_user(self):
        user = MagicMock()
        user.user_id = uuid.uuid4()
        user.tenant_id = uuid.uuid4()
        user.email = "test@example.com"
        user.role = "member"
        return user

    def test_upsert_skill_config(self):
        from app.gateway.routers.skills import SkillUpdateRequest, upsert_user_skill_config

        user = self._fake_user()
        db = AsyncMock()
        db.commit = AsyncMock()

        record = MagicMock()
        record.skill_name = "web-search"
        record.enabled = False
        record.bos_key = None

        repo_mock = MagicMock()
        repo_mock.upsert = AsyncMock(return_value=record)

        request = SkillUpdateRequest(enabled=False)

        with patch("app.gateway.routers.skills.SkillConfigRepo", return_value=repo_mock):
            result = asyncio.run(upsert_user_skill_config("web-search", request, user=user, db=db))

        assert result.skill_name == "web-search"
        assert result.enabled is False
        db.commit.assert_called_once()

    def test_delete_skill_config_not_found(self):
        from app.gateway.routers.skills import delete_user_skill_config

        user = self._fake_user()
        db = AsyncMock()

        repo_mock = MagicMock()
        repo_mock.delete = AsyncMock(return_value=False)

        with patch("app.gateway.routers.skills.SkillConfigRepo", return_value=repo_mock):
            try:
                asyncio.run(delete_user_skill_config("nonexistent", user=user, db=db))
                assert False, "Expected HTTPException"
            except Exception as e:
                assert e.status_code == 404

    def test_delete_skill_config_success(self):
        from app.gateway.routers.skills import delete_user_skill_config

        user = self._fake_user()
        db = AsyncMock()
        db.commit = AsyncMock()

        repo_mock = MagicMock()
        repo_mock.delete = AsyncMock(return_value=True)

        with patch("app.gateway.routers.skills.SkillConfigRepo", return_value=repo_mock):
            result = asyncio.run(delete_user_skill_config("web-search", user=user, db=db))

        assert result["success"] is True
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Storage factory
# ---------------------------------------------------------------------------


class TestStorageFactory:
    """Tests for get_object_storage() factory."""

    def test_local_backend(self, tmp_path):
        from crab_platform.storage import get_object_storage
        import crab_platform.storage as storage_mod

        # Reset singleton
        storage_mod._instance = None

        with patch("crab_platform.config.platform_config.get_platform_config") as mock_config:
            cfg = MagicMock()
            cfg.storage_backend = "local"
            cfg.storage_root = str(tmp_path)
            mock_config.return_value = cfg

            storage = get_object_storage()

        from crab_platform.storage.local import LocalObjectStorage

        assert isinstance(storage, LocalObjectStorage)

        # Cleanup singleton for other tests
        storage_mod._instance = None

    def test_unknown_backend_defaults_to_local(self):
        """Unknown backend falls through to LocalObjectStorage (safe default)."""
        from crab_platform.storage import get_object_storage
        import crab_platform.storage as storage_mod

        storage_mod._instance = None

        with patch("crab_platform.config.platform_config.get_platform_config") as mock_config:
            cfg = MagicMock()
            cfg.storage_backend = "unknown"
            cfg.storage_root = None
            mock_config.return_value = cfg

            storage = get_object_storage()

        from crab_platform.storage.local import LocalObjectStorage

        assert isinstance(storage, LocalObjectStorage)

        storage_mod._instance = None

    def test_oss_backend(self):
        """OSS backend returns OSSObjectStorage instance."""
        from crab_platform.storage import get_object_storage
        import crab_platform.storage as storage_mod

        storage_mod._instance = None

        with patch("crab_platform.config.platform_config.get_platform_config") as mock_config, \
             patch("crab_platform.storage.oss._get_oss_bucket") as mock_bucket:
            cfg = MagicMock()
            cfg.storage_backend = "oss"
            cfg.oss_access_key_id = "test-key"
            cfg.oss_access_key_secret = "test-secret"
            cfg.oss_endpoint = "https://oss-cn-hangzhou.aliyuncs.com"
            cfg.oss_bucket = "test-bucket"
            mock_config.return_value = cfg
            mock_bucket.return_value = MagicMock()

            storage = get_object_storage()

        from crab_platform.storage.oss import OSSObjectStorage

        assert isinstance(storage, OSSObjectStorage)

        storage_mod._instance = None


# ---------------------------------------------------------------------------
# OSSObjectStorage (mock-based)
# ---------------------------------------------------------------------------


class TestOSSObjectStorage:
    """Tests for the Alibaba Cloud OSS ObjectStorage implementation (mocked SDK)."""

    def _make_storage(self):
        from crab_platform.storage.oss import OSSObjectStorage

        with patch("crab_platform.storage.oss._get_oss_bucket") as mock_bucket, \
             patch("crab_platform.config.platform_config.get_platform_config") as mock_config:
            cfg = MagicMock()
            cfg.oss_bucket = "test-bucket"
            mock_config.return_value = cfg
            bucket_mock = MagicMock()
            mock_bucket.return_value = bucket_mock
            storage = OSSObjectStorage()
        return storage, bucket_mock

    def test_put_bytes(self):
        storage, bucket_mock = self._make_storage()
        asyncio.run(storage.put("key.txt", b"hello", "text/plain"))
        bucket_mock.put_object.assert_called_once()
        args = bucket_mock.put_object.call_args
        assert args[0][0] == "key.txt"
        assert args[0][2] == {"Content-Type": "text/plain"}

    def test_put_binary_io(self):
        storage, bucket_mock = self._make_storage()
        asyncio.run(storage.put("key.bin", BytesIO(b"data")))
        bucket_mock.put_object.assert_called_once()

    def test_put_returns_key(self):
        storage, bucket_mock = self._make_storage()
        result = asyncio.run(storage.put("my/key.txt", b"data"))
        assert result == "my/key.txt"

    def test_get(self):
        storage, bucket_mock = self._make_storage()
        stream_mock = MagicMock()
        stream_mock.read.return_value = b"content"
        bucket_mock.get_object.return_value = stream_mock
        data = asyncio.run(storage.get("key.txt"))
        assert data == b"content"
        bucket_mock.get_object.assert_called_once_with("key.txt")

    def test_delete(self):
        storage, bucket_mock = self._make_storage()
        asyncio.run(storage.delete("key.txt"))
        bucket_mock.delete_object.assert_called_once_with("key.txt")

    def test_delete_missing_no_error(self):
        storage, bucket_mock = self._make_storage()
        bucket_mock.delete_object.side_effect = Exception("not found")
        asyncio.run(storage.delete("missing.txt"))  # should not raise

    def test_generate_presigned_url(self):
        storage, bucket_mock = self._make_storage()
        bucket_mock.sign_url.return_value = "https://bucket.oss.example.com/key?signed"
        url = asyncio.run(storage.generate_presigned_url("key.txt", 7200))
        assert "signed" in url
        bucket_mock.sign_url.assert_called_once_with("GET", "key.txt", 7200)

    def test_exists_true(self):
        storage, bucket_mock = self._make_storage()
        bucket_mock.object_exists.return_value = True
        assert asyncio.run(storage.exists("key.txt")) is True

    def test_exists_false(self):
        storage, bucket_mock = self._make_storage()
        bucket_mock.object_exists.return_value = False
        assert asyncio.run(storage.exists("missing.txt")) is False

    def test_exists_exception_returns_false(self):
        storage, bucket_mock = self._make_storage()
        bucket_mock.object_exists.side_effect = Exception("network error")
        assert asyncio.run(storage.exists("key.txt")) is False
