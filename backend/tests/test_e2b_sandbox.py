"""Unit tests for E2B sandbox integration (Phase 4).

All tests mock the E2B SDK and PG — no real sandbox or database needed.
"""

from __future__ import annotations

import asyncio
import shlex
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

THREAD_ID = str(uuid.uuid4())
SANDBOX_ID = "sbx_test123"
USER_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def _make_e2b_sandbox_mock(sandbox_id: str = SANDBOX_ID) -> MagicMock:
    """Create a mock e2b.Sandbox instance."""
    sbx = MagicMock()
    sbx.sandbox_id = sandbox_id
    sbx.commands.run.return_value = MagicMock(stdout="hello\n", stderr="")
    sbx.files.read.return_value = "file content"
    sbx.files.write.return_value = None
    sbx.files.make_dir.return_value = True
    sbx.set_timeout.return_value = None
    sbx.kill.return_value = None
    return sbx


# ===========================================================================
# E2B path mapping config tests
# ===========================================================================

class TestE2BPathMappingConfig:
    def test_build_e2b_path_mapping_uses_nested_config(self):
        from crab_platform.sandbox.path_mapping import build_e2b_path_mapping

        mock_config = SimpleNamespace(
            sandbox=SimpleNamespace(
                path_mapping=SimpleNamespace(
                    user_data_dir="/home/user/runtime-data",
                    skills_dir="/home/user/runtime-skills",
                    acp_workspace_dir="/home/user/runtime-acp",
                    working_directory="/home/user/runtime-data/workspace",
                )
            ),
            skills=SimpleNamespace(container_path="/mnt/skills"),
        )

        with patch("deerflow.config.get_app_config", return_value=mock_config):
            mapping = build_e2b_path_mapping()

        assert mapping.actual_user_data_root == "/home/user/runtime-data"
        assert mapping.actual_skills_root == "/home/user/runtime-skills"
        assert mapping.actual_acp_workspace_root == "/home/user/runtime-acp"
        assert mapping.working_directory == "/home/user/runtime-data/workspace"
        assert mapping.virtual_skills_root == "/mnt/skills"

    def test_build_e2b_path_mapping_defaults_when_path_mapping_missing(self):
        from crab_platform.sandbox.path_mapping import build_e2b_path_mapping

        mock_config = SimpleNamespace(
            sandbox=SimpleNamespace(path_mapping=SimpleNamespace()),
            skills=SimpleNamespace(container_path="/mnt/skills"),
        )

        with patch("deerflow.config.get_app_config", return_value=mock_config):
            mapping = build_e2b_path_mapping()

        assert mapping.actual_user_data_root == "/home/user/.deerflow/user-data"
        assert mapping.actual_skills_root == "/home/user/.deerflow/skills"
        assert mapping.actual_acp_workspace_root == "/home/user/.deerflow/acp-workspace"
        assert mapping.working_directory == "/home/user/.deerflow/user-data/workspace"


# ===========================================================================
# E2BSandbox tests
# ===========================================================================

class TestE2BSandbox:
    """Test E2BSandbox wrapping the E2B SDK Sandbox."""

    def _make_sandbox(self, e2b_mock=None):
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        e2b_mock = e2b_mock or _make_e2b_sandbox_mock()
        return E2BSandbox(id=SANDBOX_ID, e2b_sandbox=e2b_mock), e2b_mock

    def test_id_property(self):
        sbx, _ = self._make_sandbox()
        assert sbx.id == SANDBOX_ID

    def test_execute_command(self):
        sbx, mock = self._make_sandbox()
        result = sbx.execute_command("echo hello")
        mock.commands.run.assert_called_once_with(
            "cd /home/user/.deerflow/user-data/workspace && echo hello",
            timeout=300,
        )
        assert result == "hello\n"

    def test_execute_command_no_output(self):
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="", stderr="")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.execute_command("true")
        assert result == "(no output)"

    def test_execute_command_error(self):
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.side_effect = RuntimeError("timeout")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.execute_command("hang")
        assert "Error:" in result

    def test_execute_command_stderr(self):
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="", stderr="warning\n")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.execute_command("warn")
        assert result == "warning\n"

    def test_read_file(self):
        sbx, mock = self._make_sandbox()
        result = sbx.read_file("/tmp/test.txt")
        mock.files.read.assert_called_once_with("/tmp/test.txt")
        assert result == "file content"

    def test_read_file_bytes(self):
        """read_file should handle bytes return from E2B SDK."""
        mock = _make_e2b_sandbox_mock()
        mock.files.read.return_value = b"binary content"
        sbx, _ = self._make_sandbox(mock)
        result = sbx.read_file("/tmp/bin.txt")
        assert result == "binary content"

    def test_write_file(self):
        sbx, mock = self._make_sandbox()
        sbx.write_file("/tmp/out.txt", "data")
        mock.files.write.assert_called_with("/tmp/out.txt", "data")
        mock.commands.run.assert_not_called()

    def test_write_file_append(self):
        mock = _make_e2b_sandbox_mock()
        mock.files.read.return_value = "existing "
        sbx, _ = self._make_sandbox(mock)
        sbx.write_file("/tmp/log.txt", "new", append=True)
        mock.files.write.assert_called_with("/tmp/log.txt", "existing new")

    def test_list_dir(self):
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="/tmp\n/tmp/a.txt\n/tmp/b.txt\n", stderr="")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.list_dir("/tmp")
        assert result == ["/tmp", "/tmp/a.txt", "/tmp/b.txt"]

    def test_list_dir_empty(self):
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="", stderr="")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.list_dir("/empty")
        assert result == []

    def test_update_file_binary(self):
        sbx, mock = self._make_sandbox()
        sbx.update_file("/tmp/data.bin", b"\x00\x01\x02")
        mock.files.write.assert_called_with("/tmp/data.bin", b"\x00\x01\x02")
        mock.commands.run.assert_not_called()

    def test_e2b_sandbox_property(self):
        sbx, mock = self._make_sandbox()
        assert sbx.e2b_sandbox is mock

    # -- Shell injection prevention tests -----------------------------------

    def test_list_dir_path_is_shell_quoted(self):
        """list_dir uses shlex.quote to prevent shell injection."""
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="", stderr="")
        sbx, _ = self._make_sandbox(mock)
        sbx.list_dir("/tmp/evil; rm -rf /")
        cmd = mock.commands.run.call_args[0][0]
        # shlex.quote wraps the path in single quotes
        assert "'/tmp/evil; rm -rf /'" in cmd

    def test_write_file_does_not_invoke_shell_for_path(self):
        """write_file should avoid shell mkdir and write directly via the filesystem API."""
        mock = _make_e2b_sandbox_mock()
        sbx, _ = self._make_sandbox(mock)
        sbx.write_file("/tmp/evil; rm -rf /file.txt", "data")
        mock.files.write.assert_called_once_with("/tmp/evil; rm -rf /file.txt", "data")
        mock.commands.run.assert_not_called()

    def test_update_file_does_not_invoke_shell_for_path(self):
        """update_file should avoid shell mkdir and write directly via the filesystem API."""
        mock = _make_e2b_sandbox_mock()
        sbx, _ = self._make_sandbox(mock)
        sbx.update_file("/tmp/evil; rm -rf /file.bin", b"\x00")
        mock.files.write.assert_called_once_with("/tmp/evil; rm -rf /file.bin", b"\x00")
        mock.commands.run.assert_not_called()

    def test_list_dir_max_depth_cast_to_int(self):
        """list_dir casts max_depth to int to prevent injection."""
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.return_value = MagicMock(stdout="", stderr="")
        sbx, _ = self._make_sandbox(mock)
        sbx.list_dir("/tmp", max_depth=3)
        cmd = mock.commands.run.call_args[0][0]
        assert "-maxdepth 3" in cmd


# ===========================================================================
# E2BSandboxProvider tests
# ===========================================================================

class TestE2BSandboxProvider:
    """Test E2BSandboxProvider acquire/get/release lifecycle."""

    @pytest.fixture(autouse=True)
    def _mock_config(self):
        """Mock app config so the provider can be instantiated."""
        mock_config = MagicMock()
        mock_config.sandbox.keep_alive_seconds = 1800
        mock_config.sandbox.e2b_template = None
        with patch("crab_platform.sandbox.e2b_sandbox_provider.get_app_config", return_value=mock_config):
            yield

    def _make_provider(self):
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        # Replace asyncio.Runner with a mock that runs coroutines directly
        provider._runner = MagicMock()
        provider._runner.run = lambda coro: asyncio.get_event_loop().run_until_complete(coro) if asyncio.iscoroutine(coro) else coro
        return provider

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._create_e2b_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_acquire_new_thread(self, mock_run_async, mock_create):
        """acquire() with no existing sandbox creates a new one."""
        mock_e2b = _make_e2b_sandbox_mock()
        mock_create.return_value = mock_e2b
        mock_run_async.return_value = None  # PG returns no existing sandbox

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        # First call to _run_async: _get_sandbox_id_from_pg → None
        # Subsequent calls: _update_pg_sandbox, inject_thread_uploads
        call_count = [0]
        def side_effect(coro):
            call_count[0] += 1
            if asyncio.iscoroutine(coro):
                coro.close()
            if call_count[0] == 1:
                return None  # No existing sandbox in PG
            if call_count[0] == 3:
                return 0  # inject_thread_uploads returns count
            return None

        mock_run_async.side_effect = side_effect

        result = provider.acquire(THREAD_ID)
        assert result == SANDBOX_ID
        mock_create.assert_called_once()

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._connect_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_acquire_existing_sandbox(self, mock_run_async, mock_connect):
        """acquire() reconnects to an existing sandbox from PG."""
        mock_e2b = _make_e2b_sandbox_mock()
        mock_connect.return_value = mock_e2b

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        call_count = [0]
        def side_effect(coro):
            call_count[0] += 1
            if asyncio.iscoroutine(coro):
                coro.close()
            if call_count[0] == 1:
                return SANDBOX_ID  # Existing sandbox in PG
            return None

        mock_run_async.side_effect = side_effect

        result = provider.acquire(THREAD_ID)
        assert result == SANDBOX_ID
        mock_connect.assert_called_once_with(SANDBOX_ID)

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._create_e2b_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._connect_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_acquire_reconnect_fails_creates_new(self, mock_run_async, mock_connect, mock_create):
        """acquire() creates new sandbox when reconnect to existing fails."""
        mock_connect.side_effect = RuntimeError("sandbox gone")
        mock_e2b = _make_e2b_sandbox_mock("sbx_new123")
        mock_create.return_value = mock_e2b

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        call_count = [0]
        def side_effect(coro):
            call_count[0] += 1
            if asyncio.iscoroutine(coro):
                coro.close()
            if call_count[0] == 1:
                return "sbx_dead"  # PG has an old sandbox_id
            if call_count[0] == 3:
                return 0  # inject count
            return None

        mock_run_async.side_effect = side_effect

        result = provider.acquire(THREAD_ID)
        assert result == "sbx_new123"
        mock_create.assert_called_once()

    def test_acquire_in_memory_cache(self):
        """acquire() returns cached sandbox on second call."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)

        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b
        provider._thread_to_sandbox[THREAD_ID] = SANDBOX_ID

        result = provider.acquire(THREAD_ID)
        assert result == SANDBOX_ID

    def test_get_returns_cached(self):
        """get() returns the in-memory sandbox instance."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped

        result = provider.get(SANDBOX_ID)
        assert result is wrapped

    def test_get_returns_none_for_unknown(self):
        """get() returns None for unknown sandbox_id."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        assert provider.get("nonexistent") is None

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_release_sets_timeout(self, mock_run_async):
        """release() sets keepAlive timeout and removes from cache."""
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b
        provider._thread_to_sandbox[THREAD_ID] = SANDBOX_ID

        provider.release(SANDBOX_ID)

        # Sandbox should be removed from caches
        assert SANDBOX_ID not in provider._sandboxes
        assert SANDBOX_ID not in provider._e2b_instances
        assert THREAD_ID not in provider._thread_to_sandbox

        # set_timeout should have been called
        mock_e2b.set_timeout.assert_called_once_with(1800)

    def test_shutdown_idempotent(self):
        """shutdown() is idempotent."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        provider.shutdown()
        assert provider._shutdown_called
        provider.shutdown()  # Should not raise

    def test_shutdown_sets_timeout_on_all(self):
        """shutdown() sets keepAlive on all in-memory sandboxes."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        mock1 = _make_e2b_sandbox_mock("sbx1")
        mock2 = _make_e2b_sandbox_mock("sbx2")
        provider._e2b_instances["sbx1"] = mock1
        provider._e2b_instances["sbx2"] = mock2

        provider.shutdown()

        mock1.set_timeout.assert_called_once_with(1800)
        mock2.set_timeout.assert_called_once_with(1800)
        assert len(provider._sandboxes) == 0
        assert len(provider._e2b_instances) == 0

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._connect_sandbox")
    def test_terminate_kills_sandbox(self, mock_connect, mock_run_async):
        """terminate() kills the E2B sandbox and clears PG."""
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b

        provider.terminate(SANDBOX_ID)

        mock_e2b.kill.assert_called_once()
        assert SANDBOX_ID not in provider._sandboxes
        assert SANDBOX_ID not in provider._e2b_instances

    # -- P0 fix: _run_async thread safety & shutdown guard ------------------

    def test_run_async_raises_after_shutdown(self):
        """_run_async raises RuntimeError after shutdown() has been called."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider.shutdown()

        with pytest.raises(RuntimeError, match="shut down"):
            provider._run_async(asyncio.sleep(0))

    def test_run_async_serialises_concurrent_calls(self):
        """_run_async uses _async_lock to serialise concurrent access."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()

        call_order = []

        original_run = provider._runner.run

        def slow_run(coro):
            call_order.append("enter")
            import time
            time.sleep(0.05)
            call_order.append("exit")
            if asyncio.iscoroutine(coro):
                coro.close()
            return None

        provider._runner.run = slow_run

        t1 = threading.Thread(target=lambda: provider._run_async(asyncio.sleep(0)))
        t2 = threading.Thread(target=lambda: provider._run_async(asyncio.sleep(0)))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # With serialisation, we should see enter/exit/enter/exit (not enter/enter/exit/exit)
        assert call_order == ["enter", "exit", "enter", "exit"]

    # -- P0 fix: per-thread locking in acquire() ----------------------------

    def test_acquire_per_thread_lock_prevents_double_creation(self):
        """Two concurrent acquire() calls for the same thread_id should not both create."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()

        mock_e2b = _make_e2b_sandbox_mock()
        create_count = [0]

        with (
            patch.object(provider, "_run_async") as mock_run_async,
            patch.object(provider, "_create_e2b_sandbox") as mock_create,
        ):
            def run_async_side_effect(coro):
                if asyncio.iscoroutine(coro):
                    coro.close()
                # First _run_async call per acquire = PG lookup → None (no existing)
                # Remaining calls = PG update, inject
                return None

            mock_run_async.side_effect = run_async_side_effect

            def create_side_effect():
                create_count[0] += 1
                # Second call should not happen if locking works, but
                # give a different ID so we can detect it
                return _make_e2b_sandbox_mock(f"sbx_{create_count[0]}")

            mock_create.side_effect = create_side_effect

            thread_id = str(uuid.uuid4())
            results = []

            def do_acquire():
                results.append(provider.acquire(thread_id))

            t1 = threading.Thread(target=do_acquire)
            t2 = threading.Thread(target=do_acquire)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # The second thread should hit the in-memory cache (set by the first),
            # so _create_e2b_sandbox should be called exactly once
            assert create_count[0] == 1
            # Both should return the same sandbox_id
            assert results[0] == results[1]

    def test_acquire_different_threads_not_blocked(self):
        """acquire() for different thread_ids should not block each other."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()

        with (
            patch.object(provider, "_run_async") as mock_run_async,
            patch.object(provider, "_create_e2b_sandbox") as mock_create,
        ):
            call_count = [0]

            def run_async_side_effect(coro):
                if asyncio.iscoroutine(coro):
                    coro.close()
                return None

            mock_run_async.side_effect = run_async_side_effect

            def create_side_effect():
                call_count[0] += 1
                return _make_e2b_sandbox_mock(f"sbx_{call_count[0]}")

            mock_create.side_effect = create_side_effect

            tid1 = str(uuid.uuid4())
            tid2 = str(uuid.uuid4())
            results = {}

            def do_acquire(tid):
                results[tid] = provider.acquire(tid)

            t1 = threading.Thread(target=do_acquire, args=(tid1,))
            t2 = threading.Thread(target=do_acquire, args=(tid2,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Both should succeed with different sandbox IDs
            assert len(results) == 2
            assert results[tid1] != results[tid2]
            assert call_count[0] == 2


# ===========================================================================
# FileInjector tests
# ===========================================================================

class TestFileInjector:
    """Test BOS → E2B file injection."""

    @pytest.fixture
    def mock_session_factory(self):
        """Create a mock async session factory."""
        mock_db = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        factory = MagicMock()
        factory.return_value = mock_session
        return factory, mock_db

    @pytest.mark.asyncio
    async def test_inject_no_thread(self, mock_session_factory):
        """inject_thread_uploads returns 0 when thread not found."""
        factory, mock_db = mock_session_factory

        # Lazy imports inside function body → patch at source modules
        with patch("crab_platform.db.repos.thread_repo.ThreadRepo") as MockThreadRepo:
            MockThreadRepo.return_value.get = AsyncMock(return_value=None)

            from crab_platform.sandbox.file_injector import inject_thread_uploads
            e2b_mock = _make_e2b_sandbox_mock()
            count = await inject_thread_uploads(factory, THREAD_ID, e2b_mock)
            assert count == 0

    @pytest.mark.asyncio
    async def test_inject_no_uploads(self, mock_session_factory):
        """inject_thread_uploads returns 0 when no uploads exist."""
        factory, mock_db = mock_session_factory

        mock_thread = MagicMock()
        mock_thread.user_id = USER_ID

        with (
            patch("crab_platform.db.repos.thread_repo.ThreadRepo") as MockThreadRepo,
            patch("crab_platform.db.repos.upload_repo.UploadRepo") as MockUploadRepo,
        ):
            MockThreadRepo.return_value.get = AsyncMock(return_value=mock_thread)
            MockUploadRepo.return_value.list_for_thread = AsyncMock(return_value=[])

            from crab_platform.sandbox.file_injector import inject_thread_uploads
            e2b_mock = _make_e2b_sandbox_mock()
            count = await inject_thread_uploads(factory, THREAD_ID, e2b_mock)
            assert count == 0

    @pytest.mark.asyncio
    async def test_inject_uploads_success(self, mock_session_factory):
        """inject_thread_uploads writes files from BOS to E2B."""
        factory, mock_db = mock_session_factory

        mock_thread = MagicMock()
        mock_thread.user_id = USER_ID

        upload1 = MagicMock()
        upload1.filename = "data.csv"
        upload1.bos_key = "tenant/user/uploads/thread/data.csv"
        upload1.markdown_bos_key = None

        upload2 = MagicMock()
        upload2.filename = "report.pdf"
        upload2.bos_key = "tenant/user/uploads/thread/report.pdf"
        upload2.markdown_bos_key = "tenant/user/uploads/thread/report.pdf.md"

        mock_storage = AsyncMock()
        mock_storage.get = AsyncMock(side_effect=[
            b"csv content",
            b"pdf content",
            b"markdown content",
        ])

        with (
            patch("crab_platform.db.repos.thread_repo.ThreadRepo") as MockThreadRepo,
            patch("crab_platform.db.repos.upload_repo.UploadRepo") as MockUploadRepo,
            patch("crab_platform.storage.get_object_storage", return_value=mock_storage),
        ):
            MockThreadRepo.return_value.get = AsyncMock(return_value=mock_thread)
            MockUploadRepo.return_value.list_for_thread = AsyncMock(return_value=[upload1, upload2])

            from crab_platform.sandbox.file_injector import inject_thread_uploads
            e2b_mock = _make_e2b_sandbox_mock()
            count = await inject_thread_uploads(factory, THREAD_ID, e2b_mock)

            assert count == 3  # data.csv + report.pdf + report.md
            assert e2b_mock.files.write.call_count == 3

            # Check the paths written
            calls = e2b_mock.files.write.call_args_list
            paths_written = [c[0][0] for c in calls]
            assert "/home/user/.deerflow/user-data/uploads/data.csv" in paths_written
            assert "/home/user/.deerflow/user-data/uploads/report.pdf" in paths_written
            assert "/home/user/.deerflow/user-data/uploads/report.pdf.extracted.md" in paths_written

    @pytest.mark.asyncio
    async def test_inject_partial_failure(self, mock_session_factory):
        """inject_thread_uploads continues on individual file failure."""
        factory, mock_db = mock_session_factory

        mock_thread = MagicMock()
        mock_thread.user_id = USER_ID

        upload1 = MagicMock()
        upload1.filename = "good.txt"
        upload1.bos_key = "key1"
        upload1.markdown_bos_key = None

        upload2 = MagicMock()
        upload2.filename = "bad.txt"
        upload2.bos_key = "key2"
        upload2.markdown_bos_key = None

        mock_storage = AsyncMock()
        mock_storage.get = AsyncMock(side_effect=[b"good content", RuntimeError("BOS error")])

        with (
            patch("crab_platform.db.repos.thread_repo.ThreadRepo") as MockThreadRepo,
            patch("crab_platform.db.repos.upload_repo.UploadRepo") as MockUploadRepo,
            patch("crab_platform.storage.get_object_storage", return_value=mock_storage),
        ):
            MockThreadRepo.return_value.get = AsyncMock(return_value=mock_thread)
            MockUploadRepo.return_value.list_for_thread = AsyncMock(return_value=[upload1, upload2])

            from crab_platform.sandbox.file_injector import inject_thread_uploads
            e2b_mock = _make_e2b_sandbox_mock()
            count = await inject_thread_uploads(factory, THREAD_ID, e2b_mock)

            assert count == 1  # Only good.txt succeeded


# ===========================================================================
# SandboxCleaner tests
# ===========================================================================

class TestSandboxCleaner:
    """Test the background sandbox cleaner."""

    def test_start_stop(self):
        """Cleaner starts and stops cleanly."""
        from crab_platform.sandbox.cleaner import SandboxCleaner
        cleaner = SandboxCleaner(ttl_hours=24, check_interval_minutes=1)
        # Don't actually start the loop — just verify it doesn't blow up
        cleaner._stop_event.set()  # Pre-set so loop exits immediately
        cleaner.start()
        cleaner.stop()

    def test_start_idempotent(self):
        """Calling start() twice is safe."""
        from crab_platform.sandbox.cleaner import SandboxCleaner
        cleaner = SandboxCleaner(ttl_hours=24, check_interval_minutes=1)
        cleaner._stop_event.set()
        cleaner.start()
        cleaner.start()  # Should not create a second thread
        cleaner.stop()

    @pytest.mark.asyncio
    async def test_cleanup_stale_sandboxes(self):
        """_cleanup_stale_sandboxes finds and terminates stale entries."""
        from crab_platform.sandbox.cleaner import SandboxCleaner

        mock_db = AsyncMock()
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_session)

        cleaner = SandboxCleaner(session_factory=mock_factory, ttl_hours=24)

        thread_id = uuid.uuid4()
        sandbox_id = "sbx_stale"

        # Mock PG query returning one stale sandbox
        mock_result = MagicMock()
        mock_result.all.return_value = [(thread_id, sandbox_id, "active")]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        with patch("crab_platform.sandbox.cleaner.SandboxCleaner._terminate_sandbox", new_callable=AsyncMock) as mock_terminate:
            await cleaner._cleanup_stale_sandboxes()
            mock_terminate.assert_called_once_with(mock_db, thread_id, sandbox_id)

    @pytest.mark.asyncio
    async def test_terminate_sandbox_kills_and_updates_pg(self):
        """_terminate_sandbox connects, kills, and clears PG."""
        from crab_platform.sandbox.cleaner import SandboxCleaner

        mock_factory = AsyncMock()
        cleaner = SandboxCleaner(session_factory=mock_factory)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        mock_e2b = _make_e2b_sandbox_mock()

        with patch("e2b.Sandbox") as MockE2B:
            MockE2B.connect.return_value = mock_e2b

            thread_id = uuid.uuid4()
            await cleaner._terminate_sandbox(mock_db, thread_id, SANDBOX_ID)

            MockE2B.connect.assert_called_once_with(SANDBOX_ID)
            mock_e2b.kill.assert_called_once()
            # PG update should have been called
            assert mock_db.execute.called


# ===========================================================================
# Integration: Sandbox ABC compliance
# ===========================================================================

# ===========================================================================
# P2: Additional coverage — E2BSandbox error paths
# ===========================================================================

class TestE2BSandboxErrorPaths:
    """P2 #14-16: Error paths for E2BSandbox methods."""

    def _make_sandbox(self, e2b_mock=None):
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        e2b_mock = e2b_mock or _make_e2b_sandbox_mock()
        return E2BSandbox(id=SANDBOX_ID, e2b_sandbox=e2b_mock), e2b_mock

    def test_read_file_propagates_exception(self):
        """#14: read_file raises when E2B SDK raises."""
        mock = _make_e2b_sandbox_mock()
        mock.files.read.side_effect = RuntimeError("not found")
        sbx, _ = self._make_sandbox(mock)
        with pytest.raises(RuntimeError, match="not found"):
            sbx.read_file("/no/such/file")

    def test_write_file_propagates_exception(self):
        """#14: write_file raises when E2B SDK write raises."""
        mock = _make_e2b_sandbox_mock()
        mock.files.write.side_effect = RuntimeError("disk full")
        sbx, _ = self._make_sandbox(mock)
        with pytest.raises(RuntimeError, match="disk full"):
            sbx.write_file("/tmp/file.txt", "data")

    def test_update_file_propagates_exception(self):
        """#14: update_file raises when E2B SDK write raises."""
        mock = _make_e2b_sandbox_mock()
        mock.files.write.side_effect = RuntimeError("disk full")
        sbx, _ = self._make_sandbox(mock)
        with pytest.raises(RuntimeError, match="disk full"):
            sbx.update_file("/tmp/file.bin", b"\x00")

    def test_list_dir_returns_empty_on_error(self):
        """#15: list_dir returns [] on E2B SDK failure."""
        mock = _make_e2b_sandbox_mock()
        mock.commands.run.side_effect = RuntimeError("timeout")
        sbx, _ = self._make_sandbox(mock)
        result = sbx.list_dir("/tmp")
        assert result == []

    def test_write_file_append_falls_back_on_read_error(self):
        """#16: write_file append=True falls back to empty string if read fails."""
        mock = _make_e2b_sandbox_mock()
        mock.files.read.side_effect = RuntimeError("file not found")
        sbx, _ = self._make_sandbox(mock)
        sbx.write_file("/tmp/new.txt", "content", append=True)
        # Should write just the new content (empty + "content")
        mock.files.write.assert_called_with("/tmp/new.txt", "content")


# ===========================================================================
# P2: Additional coverage — E2BSandboxProvider edge cases
# ===========================================================================

class TestE2BSandboxProviderEdgeCases:
    """P2 #11-13, #19-20: Edge cases for E2BSandboxProvider."""

    @pytest.fixture(autouse=True)
    def _mock_config(self):
        mock_config = MagicMock()
        mock_config.sandbox.keep_alive_seconds = 1800
        mock_config.sandbox.e2b_template = None
        with patch("crab_platform.sandbox.e2b_sandbox_provider.get_app_config", return_value=mock_config):
            yield

    def test_acquire_anonymous_sandbox(self):
        """#11: acquire(thread_id=None) creates anonymous sandbox."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        mock_e2b = _make_e2b_sandbox_mock()

        with patch.object(provider, "_create_e2b_sandbox", return_value=mock_e2b):
            result = provider.acquire(None)

        assert result == SANDBOX_ID
        assert provider.get(SANDBOX_ID) is not None
        # Anonymous sandbox should NOT be in thread_to_sandbox mapping
        assert SANDBOX_ID not in provider._thread_to_sandbox.values() or \
               all(v != SANDBOX_ID for k, v in provider._thread_to_sandbox.items() if k is not None)

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._connect_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_terminate_not_in_memory_connects_and_kills(self, mock_run_async, mock_connect):
        """#12: terminate() when sandbox not in memory tries connect-and-kill."""
        mock_e2b = _make_e2b_sandbox_mock()
        mock_connect.return_value = mock_e2b
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        # Sandbox is NOT in memory
        assert SANDBOX_ID not in provider._sandboxes
        assert SANDBOX_ID not in provider._e2b_instances

        provider.terminate(SANDBOX_ID)

        mock_connect.assert_called_once_with(SANDBOX_ID)
        mock_e2b.kill.assert_called_once()
        # PG clear should have been called
        assert mock_run_async.called

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._connect_sandbox")
    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_terminate_not_in_memory_connect_fails(self, mock_run_async, mock_connect):
        """#12: terminate() when connect fails still clears PG."""
        mock_connect.side_effect = RuntimeError("sandbox gone")
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        # Should not raise
        provider.terminate(SANDBOX_ID)

        mock_connect.assert_called_once_with(SANDBOX_ID)
        # PG clear should still be called
        assert mock_run_async.called

    def test_release_unknown_sandbox_does_not_raise(self):
        """#13: release() for unknown sandbox_id is a no-op (no exception)."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        with patch.object(provider, "_run_async", return_value=None):
            # Should not raise
            provider.release("nonexistent_sandbox_id")

    def test_release_set_timeout_failure_keeps_sandbox_in_cache(self):
        """#13: release() keeps sandbox in cache when set_timeout fails."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        mock_e2b = _make_e2b_sandbox_mock()
        mock_e2b.set_timeout.side_effect = RuntimeError("E2B API error")

        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b
        provider._thread_to_sandbox[THREAD_ID] = SANDBOX_ID

        with patch.object(provider, "_run_async", return_value=None):
            provider.release(SANDBOX_ID)

        # Sandbox should STILL be in cache since set_timeout failed
        assert SANDBOX_ID in provider._sandboxes
        assert SANDBOX_ID in provider._e2b_instances
        assert THREAD_ID in provider._thread_to_sandbox

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_release_calls_touch_sandbox_last_seen(self, mock_run_async):
        """#19: release() calls _touch_sandbox_last_seen via _run_async."""
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b

        provider.release(SANDBOX_ID)

        # set_timeout is called first, then _run_async for PG touch
        mock_e2b.set_timeout.assert_called_once_with(1800)
        # _run_async should be called at least once (for _touch_sandbox_last_seen)
        assert mock_run_async.called

    @patch("crab_platform.sandbox.e2b_sandbox_provider.E2BSandboxProvider._run_async")
    def test_terminate_calls_clear_pg_sandbox(self, mock_run_async):
        """#20: terminate() calls _clear_pg_sandbox via _run_async."""
        mock_run_async.return_value = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()
        provider._runner.run = mock_run_async

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b

        provider.terminate(SANDBOX_ID)

        mock_e2b.kill.assert_called_once()
        # _run_async should be called for _clear_pg_sandbox
        assert mock_run_async.called

    def test_evict_from_cache_clears_all_mappings(self):
        """_evict_from_cache removes sandbox from all three caches."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        mock_e2b = _make_e2b_sandbox_mock()
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        wrapped = E2BSandbox(id=SANDBOX_ID, e2b_sandbox=mock_e2b)
        provider._sandboxes[SANDBOX_ID] = wrapped
        provider._e2b_instances[SANDBOX_ID] = mock_e2b
        tid1 = str(uuid.uuid4())
        provider._thread_to_sandbox[tid1] = SANDBOX_ID

        result = provider._evict_from_cache(SANDBOX_ID)

        assert result is mock_e2b
        assert SANDBOX_ID not in provider._sandboxes
        assert SANDBOX_ID not in provider._e2b_instances
        assert tid1 not in provider._thread_to_sandbox

    def test_evict_from_cache_unknown_returns_none(self):
        """_evict_from_cache returns None for unknown sandbox."""
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        provider._runner = MagicMock()

        result = provider._evict_from_cache("nonexistent")
        assert result is None


# ===========================================================================
# P2: Additional coverage — SandboxCleaner edge cases
# ===========================================================================

class TestSandboxCleanerEdgeCases:
    """P2 #17-18: Edge cases for SandboxCleaner."""

    @pytest.mark.asyncio
    async def test_terminate_sandbox_connect_fails_still_clears_pg(self):
        """#17: _terminate_sandbox clears PG even when E2B connect fails."""
        from crab_platform.sandbox.cleaner import SandboxCleaner

        mock_factory = AsyncMock()
        cleaner = SandboxCleaner(session_factory=mock_factory)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        with patch("e2b.Sandbox") as MockE2B:
            MockE2B.connect.side_effect = RuntimeError("sandbox already gone")

            thread_id = uuid.uuid4()
            await cleaner._terminate_sandbox(mock_db, thread_id, SANDBOX_ID)

            MockE2B.connect.assert_called_once_with(SANDBOX_ID)
            # PG update should STILL have been called despite connect failure
            assert mock_db.execute.called

    @pytest.mark.asyncio
    async def test_cleanup_no_stale_sandboxes(self):
        """#18: _cleanup_stale_sandboxes is a no-op when nothing is stale."""
        from crab_platform.sandbox.cleaner import SandboxCleaner

        mock_db = AsyncMock()
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_session)

        cleaner = SandboxCleaner(session_factory=mock_factory, ttl_hours=24)

        # PG query returns no stale sandboxes
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("crab_platform.sandbox.cleaner.SandboxCleaner._terminate_sandbox", new_callable=AsyncMock) as mock_terminate:
            await cleaner._cleanup_stale_sandboxes()
            mock_terminate.assert_not_called()
            # db.commit should NOT be called (early return)
            mock_db.commit.assert_not_called()


# ===========================================================================
# P2: Additional coverage — FileInjector edge cases
# ===========================================================================

class TestFileInjectorEdgeCases:
    """P2: Additional edge cases for file injection."""

    @pytest.fixture
    def mock_session_factory(self):
        mock_db = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        factory = MagicMock()
        factory.return_value = mock_session
        return factory, mock_db

    @pytest.mark.asyncio
    async def test_inject_markdown_failure_does_not_fail_main_file(self, mock_session_factory):
        """Markdown companion injection failure doesn't prevent the main file from being counted."""
        factory, mock_db = mock_session_factory

        mock_thread = MagicMock()
        mock_thread.user_id = USER_ID

        upload = MagicMock()
        upload.filename = "doc.pdf"
        upload.bos_key = "key/doc.pdf"
        upload.markdown_bos_key = "key/doc.pdf.md"

        mock_storage = AsyncMock()
        # First get() succeeds (main file), second get() fails (markdown)
        mock_storage.get = AsyncMock(side_effect=[b"pdf data", RuntimeError("BOS error")])

        with (
            patch("crab_platform.db.repos.thread_repo.ThreadRepo") as MockThreadRepo,
            patch("crab_platform.db.repos.upload_repo.UploadRepo") as MockUploadRepo,
            patch("crab_platform.storage.get_object_storage", return_value=mock_storage),
        ):
            MockThreadRepo.return_value.get = AsyncMock(return_value=mock_thread)
            MockUploadRepo.return_value.list_for_thread = AsyncMock(return_value=[upload])

            from crab_platform.sandbox.file_injector import inject_thread_uploads
            e2b_mock = _make_e2b_sandbox_mock()
            count = await inject_thread_uploads(factory, THREAD_ID, e2b_mock)

            # Main file should be counted, markdown failure doesn't affect it
            assert count == 1


# ===========================================================================
# Integration: Sandbox ABC compliance
# ===========================================================================

class TestSandboxABCCompliance:
    """Verify E2BSandbox correctly implements the Sandbox ABC."""

    def test_is_subclass_of_sandbox(self):
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        from deerflow.sandbox.sandbox import Sandbox
        assert issubclass(E2BSandbox, Sandbox)

    def test_all_abstract_methods_implemented(self):
        """E2BSandbox should be instantiable (all abstract methods implemented)."""
        from crab_platform.sandbox.e2b_sandbox import E2BSandbox
        mock_e2b = _make_e2b_sandbox_mock()
        sbx = E2BSandbox(id="test", e2b_sandbox=mock_e2b)
        assert hasattr(sbx, "execute_command")
        assert hasattr(sbx, "read_file")
        assert hasattr(sbx, "write_file")
        assert hasattr(sbx, "list_dir")
        assert hasattr(sbx, "update_file")


class TestSandboxProviderABCCompliance:
    """Verify E2BSandboxProvider correctly implements the SandboxProvider ABC."""

    def test_is_subclass_of_sandbox_provider(self):
        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        from deerflow.sandbox.sandbox_provider import SandboxProvider
        assert issubclass(E2BSandboxProvider, SandboxProvider)

    @patch("crab_platform.sandbox.e2b_sandbox_provider.get_app_config")
    def test_all_abstract_methods_implemented(self, mock_config):
        mock_config.return_value.sandbox.keep_alive_seconds = 1800
        mock_config.return_value.sandbox.e2b_template = None

        from crab_platform.sandbox.e2b_sandbox_provider import E2BSandboxProvider
        provider = E2BSandboxProvider()
        assert hasattr(provider, "acquire")
        assert hasattr(provider, "get")
        assert hasattr(provider, "release")
        provider._runner.close()
