"""E2BSandboxProvider — PG-backed lifecycle management for E2B cloud sandboxes.

Implements the harness ``SandboxProvider`` ABC (acquire / get / release) and
adds ``terminate()`` and ``shutdown()`` for full lifecycle control.

Design decisions:
- Provider is a **sync singleton** (created by ``get_sandbox_provider()``),
  but needs async PG access.  We bridge with ``asyncio.Runner`` (Python 3.12+)
  protected by a dedicated lock (``_async_lock``).
- ``release()`` does NOT kill the sandbox — it sets a keepAlive timeout so
  E2B auto-pauses after inactivity.  This matches the harness
  ``SandboxMiddleware.after_agent`` semantics.
- ``connect()`` auto-resumes paused E2B instances — no explicit resume needed.
- Thread → sandbox mapping is persisted in PG ``threads`` table, not in-memory
  (survives Gateway restarts).
- Per-thread locks in ``acquire()`` prevent concurrent sandbox creation for the
  same thread_id.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from deerflow.config import get_app_config
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider

from crab_platform.sandbox.e2b_sandbox import E2BSandbox
from crab_platform.sandbox.path_mapping import E2BPathMapping, build_e2b_path_mapping

if TYPE_CHECKING:
    from e2b import Sandbox as E2BSdkSandbox
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_KEEP_ALIVE_SECONDS = 1800  # 30 minutes


class E2BSandboxProvider(SandboxProvider):
    """E2B cloud sandbox provider with PG-backed thread ↔ sandbox mapping.

    Configured via ``config.yaml``:

    .. code-block:: yaml

        sandbox:
          use: crab_platform.sandbox:E2BSandboxProvider
          keep_alive_seconds: 1800   # optional, default 30min
          e2b_template: "base"       # optional E2B template
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._async_lock = threading.Lock()  # serialises all _run_async calls
        self._acquire_locks: dict[str, threading.Lock] = {}  # per-thread_id locks
        self._acquire_locks_guard = threading.Lock()  # protects _acquire_locks dict
        # In-memory caches (process-scoped, not durable)
        self._sandboxes: dict[str, E2BSandbox] = {}  # sandbox_id → E2BSandbox
        self._e2b_instances: dict[str, E2BSdkSandbox] = {}  # sandbox_id → raw E2B
        self._thread_to_sandbox: dict[str, str] = {}  # thread_id → sandbox_id

        self._runner = asyncio.Runner()
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._shutdown_called = False

        # Read config (SandboxConfig has extra="allow")
        sandbox_config = get_app_config().sandbox
        self._keep_alive_seconds = int(
            getattr(sandbox_config, "keep_alive_seconds", _DEFAULT_KEEP_ALIVE_SECONDS)
        )
        self._e2b_template = getattr(sandbox_config, "e2b_template", None) or None
        self._e2b_api_key = getattr(sandbox_config, "e2b_api_key", None) or None
        self._e2b_api_url = getattr(sandbox_config, "e2b_api_url", None) or None
        self._path_mapping = build_e2b_path_mapping()

    # -- Lazy dependencies --------------------------------------------------

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            from crab_platform.db import get_session_factory
            self._session_factory = get_session_factory()
        return self._session_factory

    def _run_async(self, coro):
        """Run an async coroutine synchronously.

        Thread-safe: serialised via ``_async_lock``.
        Shutdown-safe: raises ``RuntimeError`` after ``shutdown()`` has been called.
        """
        with self._async_lock:
            if self._shutdown_called:
                raise RuntimeError("E2BSandboxProvider has been shut down")
            return self._runner.run(coro)

    # -- SandboxProvider ABC ------------------------------------------------

    def _get_thread_lock(self, thread_id: str) -> threading.Lock:
        """Return (or create) a per-thread_id lock to serialise acquire() calls."""
        with self._acquire_locks_guard:
            lock = self._acquire_locks.get(thread_id)
            if lock is None:
                lock = threading.Lock()
                self._acquire_locks[thread_id] = lock
            return lock

    def acquire(self, thread_id: str | None = None) -> str:
        """Acquire an E2B sandbox for the given thread.

        1. Check in-memory cache.
        2. Check PG for an existing sandbox_id → try ``connect()`` (auto-resumes).
        3. If no existing sandbox or connect fails → create a new one.

        Per-thread locking prevents two concurrent requests for the same
        ``thread_id`` from each creating a new sandbox (which would orphan one).
        """
        if thread_id is None:
            return self._create_anonymous_sandbox()

        thread_lock = self._get_thread_lock(thread_id)
        with thread_lock:
            return self._acquire_for_thread(thread_id)

    def _acquire_for_thread(self, thread_id: str) -> str:
        """Actual acquire logic, called under per-thread lock."""
        # 1. In-memory cache (fast path — same process, same turn or subsequent)
        with self._lock:
            cached_id = self._thread_to_sandbox.get(thread_id)
            if cached_id and cached_id in self._sandboxes:
                logger.info("Reusing in-memory E2B sandbox %s for thread %s", cached_id, thread_id)
                return cached_id

        # 2. Check PG for existing sandbox
        existing_sandbox_id = self._run_async(self._get_sandbox_id_from_pg(thread_id))

        if existing_sandbox_id:
            # Check if we already have it in-memory (race with another thread)
            with self._lock:
                if existing_sandbox_id in self._sandboxes:
                    self._thread_to_sandbox[thread_id] = existing_sandbox_id
                    return existing_sandbox_id

            # Try to connect (auto-resumes paused instances)
            try:
                e2b_sbx = self._connect_sandbox(existing_sandbox_id)
                wrapped = E2BSandbox(
                    id=existing_sandbox_id,
                    e2b_sandbox=e2b_sbx,
                    path_mapping=self._path_mapping,
                )
                with self._lock:
                    self._sandboxes[existing_sandbox_id] = wrapped
                    self._e2b_instances[existing_sandbox_id] = e2b_sbx
                    self._thread_to_sandbox[thread_id] = existing_sandbox_id
                self._run_async(
                    self._update_pg_sandbox(thread_id, existing_sandbox_id, "active")
                )
                logger.info("Reconnected to E2B sandbox %s for thread %s", existing_sandbox_id, thread_id)
                return existing_sandbox_id
            except Exception:
                logger.info(
                    "Could not reconnect to E2B sandbox %s, creating new one",
                    existing_sandbox_id,
                    exc_info=True,
                )

        # 3. Create new sandbox
        return self._create_and_register(thread_id)

    def get(self, sandbox_id: str) -> Sandbox | None:
        with self._lock:
            return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        """Release a sandbox: set keepAlive timeout but do NOT kill.

        The E2B sandbox will auto-pause after the timeout expires.
        The SandboxCleaner will eventually terminate truly stale sandboxes.

        Sets the keepAlive timeout *before* evicting from cache so that, if
        ``set_timeout`` fails, the sandbox remains tracked and can be reused.
        """
        with self._lock:
            e2b_sbx = self._e2b_instances.get(sandbox_id)

        if e2b_sbx is not None:
            try:
                e2b_sbx.set_timeout(self._keep_alive_seconds)
                logger.info(
                    "Released E2B sandbox %s (keepAlive %ds)", sandbox_id, self._keep_alive_seconds
                )
            except Exception:
                logger.warning(
                    "Failed to set timeout on E2B sandbox %s; keeping in cache",
                    sandbox_id,
                    exc_info=True,
                )
                return  # Don't evict — sandbox stays trackable for next acquire()

        self._evict_from_cache(sandbox_id)

        # Update PG last_seen_at (best-effort)
        try:
            self._run_async(self._touch_sandbox_last_seen(sandbox_id))
        except Exception:
            logger.debug("Failed to update last_seen_at for %s", sandbox_id, exc_info=True)

    # -- Extended lifecycle -------------------------------------------------

    def terminate(self, sandbox_id: str) -> None:
        """Terminate (kill) an E2B sandbox and clear PG fields.

        Used by ``SandboxCleaner`` and for explicit cleanup.
        """
        # Remove from in-memory caches
        e2b_sbx = self._evict_from_cache(sandbox_id)

        # Kill the E2B sandbox
        if e2b_sbx is not None:
            try:
                e2b_sbx.kill()
                logger.info("Terminated E2B sandbox %s", sandbox_id)
            except Exception:
                logger.debug("Failed to kill E2B sandbox %s", sandbox_id, exc_info=True)
        else:
            # Try to connect and kill (sandbox may be paused)
            try:
                sbx = self._connect_sandbox(sandbox_id)
                sbx.kill()
                logger.info("Connected and terminated E2B sandbox %s", sandbox_id)
            except Exception:
                logger.debug("Could not connect/kill E2B sandbox %s", sandbox_id, exc_info=True)

        # Clear PG
        try:
            self._run_async(self._clear_pg_sandbox(sandbox_id))
        except Exception:
            logger.debug("Failed to clear PG for sandbox %s", sandbox_id, exc_info=True)

    def shutdown(self) -> None:
        """Graceful shutdown: set keepAlive on all in-memory sandboxes, close runner."""
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            instances = list(self._e2b_instances.items())
            self._sandboxes.clear()
            self._e2b_instances.clear()
            self._thread_to_sandbox.clear()

        for sandbox_id, e2b_sbx in instances:
            try:
                e2b_sbx.set_timeout(self._keep_alive_seconds)
                logger.info("Shutdown: set keepAlive on E2B sandbox %s", sandbox_id)
            except Exception:
                logger.debug("Shutdown: failed to set timeout on %s", sandbox_id, exc_info=True)

        try:
            self._runner.close()
        except Exception:
            pass

        logger.info("E2BSandboxProvider shutdown complete")

    # -- Internal helpers ---------------------------------------------------

    def _evict_from_cache(self, sandbox_id: str) -> E2BSdkSandbox | None:
        """Remove a sandbox from all in-memory caches.

        Returns the raw E2B SDK instance (or ``None`` if not found).
        """
        with self._lock:
            e2b_sbx = self._e2b_instances.pop(sandbox_id, None)
            self._sandboxes.pop(sandbox_id, None)
            thread_ids = [tid for tid, sid in self._thread_to_sandbox.items() if sid == sandbox_id]
            for tid in thread_ids:
                del self._thread_to_sandbox[tid]
        return e2b_sbx

    def _e2b_api_opts(self) -> dict:
        """Build common E2B SDK ``**opts`` (api_key, api_url) from config."""
        opts: dict = {}
        if self._e2b_api_key:
            opts["api_key"] = self._e2b_api_key
        if self._e2b_api_url:
            opts["api_url"] = self._e2b_api_url
        return opts

    def _connect_sandbox(self, sandbox_id: str) -> E2BSdkSandbox:
        """Connect to an existing E2B sandbox (auto-resumes paused instances)."""
        from e2b import Sandbox as E2BSdkSandbox
        return E2BSdkSandbox.connect(sandbox_id, **self._e2b_api_opts())

    def _create_e2b_sandbox(self) -> E2BSdkSandbox:
        """Create a new E2B sandbox via the SDK."""
        from e2b import Sandbox as E2BSdkSandbox

        kwargs = self._e2b_api_opts()
        if self._e2b_template:
            kwargs["template"] = self._e2b_template

        return E2BSdkSandbox.create(**kwargs)

    def _create_anonymous_sandbox(self) -> str:
        """Create a sandbox without thread association (rare — thread_id should always be provided)."""
        e2b_sbx = self._create_e2b_sandbox()
        sandbox_id = e2b_sbx.sandbox_id
        self._init_sandbox_dirs(e2b_sbx, self._path_mapping)
        wrapped = E2BSandbox(
            id=sandbox_id,
            e2b_sandbox=e2b_sbx,
            path_mapping=self._path_mapping,
        )
        with self._lock:
            self._sandboxes[sandbox_id] = wrapped
            self._e2b_instances[sandbox_id] = e2b_sbx
        logger.info("Created anonymous E2B sandbox %s", sandbox_id)
        return sandbox_id

    def _create_and_register(self, thread_id: str) -> str:
        """Create a new E2B sandbox, register in PG and inject files."""
        e2b_sbx = self._create_e2b_sandbox()
        sandbox_id = e2b_sbx.sandbox_id

        # Initialize the actual E2B working directory structure.
        self._init_sandbox_dirs(e2b_sbx, self._path_mapping)

        wrapped = E2BSandbox(
            id=sandbox_id,
            e2b_sandbox=e2b_sbx,
            path_mapping=self._path_mapping,
        )
        with self._lock:
            self._sandboxes[sandbox_id] = wrapped
            self._e2b_instances[sandbox_id] = e2b_sbx
            self._thread_to_sandbox[thread_id] = sandbox_id

        # Persist to PG
        self._run_async(self._update_pg_sandbox(thread_id, sandbox_id, "active"))

        # Inject uploaded files from BOS
        try:
            from crab_platform.sandbox.file_injector import inject_thread_uploads
            count = self._run_async(
                inject_thread_uploads(
                    self._get_session_factory(),
                    thread_id,
                    e2b_sbx,
                    path_mapping=self._path_mapping,
                )
            )
            if count > 0:
                logger.info("Injected %d files into E2B sandbox %s", count, sandbox_id)
        except Exception:
            logger.warning("Failed to inject files into E2B sandbox %s", sandbox_id, exc_info=True)

        # Inject custom user skill directories so prompt-advertised skills are executable.
        try:
            from crab_platform.db import get_session_factory
            from crab_platform.db.repos.thread_repo import ThreadRepo
            from crab_platform.sandbox.file_injector import inject_user_custom_skills

            async def _load_user_id() -> uuid.UUID | None:
                async with get_session_factory()() as db:
                    thread = await ThreadRepo(db).get(uuid.UUID(thread_id))
                    return thread.user_id if thread is not None else None

            user_id = self._run_async(_load_user_id())
            if user_id is not None:
                skill_count = self._run_async(
                    inject_user_custom_skills(
                        self._get_session_factory(),
                        user_id,
                        e2b_sbx,
                        path_mapping=self._path_mapping,
                    )
                )
                if skill_count > 0:
                    logger.info("Injected %d custom skill files into E2B sandbox %s", skill_count, sandbox_id)
        except Exception:
            logger.warning("Failed to inject custom skills into E2B sandbox %s", sandbox_id, exc_info=True)

        logger.info("Created E2B sandbox %s for thread %s", sandbox_id, thread_id)
        return sandbox_id

    @staticmethod
    def _init_sandbox_dirs(
        e2b_sbx: E2BSdkSandbox,
        path_mapping: E2BPathMapping,
    ) -> None:
        """Create the actual writable directory tree inside the sandbox."""
        for path in (
            path_mapping.actual_workspace_dir,
            path_mapping.actual_uploads_dir,
            path_mapping.actual_outputs_dir,
            path_mapping.actual_custom_skills_dir,
            path_mapping.actual_acp_workspace_root,
        ):
            e2b_sbx.files.make_dir(path)

    # -- PG helpers (async, called via _run_async) --------------------------

    async def _get_sandbox_id_from_pg(self, thread_id: str) -> str | None:
        """Look up the sandbox_id for a thread from PG."""
        from crab_platform.db.models import Thread

        factory = self._get_session_factory()
        tid = uuid.UUID(thread_id)

        async with factory() as db:
            result = await db.execute(
                select(Thread.sandbox_id, Thread.sandbox_status)
                .where(Thread.id == tid)
            )
            row = result.one_or_none()
            if row is None:
                return None
            sandbox_id, status = row
            if sandbox_id and status in ("active", "paused", None):
                return sandbox_id
            return None

    async def _update_pg_sandbox(
        self, thread_id: str, sandbox_id: str, status: str
    ) -> None:
        """Update the sandbox fields on the thread row in PG."""
        from crab_platform.db.models import Thread

        factory = self._get_session_factory()
        tid = uuid.UUID(thread_id)

        async with factory() as db:
            await db.execute(
                update(Thread)
                .where(Thread.id == tid)
                .values(
                    sandbox_id=sandbox_id,
                    sandbox_status=status,
                    sandbox_last_seen_at=datetime.now(UTC),
                )
            )
            await db.commit()

    async def _touch_sandbox_last_seen(self, sandbox_id: str) -> None:
        """Update sandbox_last_seen_at for the thread that owns this sandbox."""
        from crab_platform.db.models import Thread

        factory = self._get_session_factory()

        async with factory() as db:
            await db.execute(
                update(Thread)
                .where(Thread.sandbox_id == sandbox_id)
                .values(sandbox_last_seen_at=datetime.now(UTC))
            )
            await db.commit()

    async def _clear_pg_sandbox(self, sandbox_id: str) -> None:
        """Clear sandbox fields for the thread that owns this sandbox."""
        from crab_platform.db.models import Thread

        factory = self._get_session_factory()

        async with factory() as db:
            await db.execute(
                update(Thread)
                .where(Thread.sandbox_id == sandbox_id)
                .values(
                    sandbox_id=None,
                    sandbox_status="terminated",
                    sandbox_last_seen_at=None,
                )
            )
            await db.commit()
