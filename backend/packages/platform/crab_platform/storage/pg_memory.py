"""PostgreSQL-backed memory storage for multi-tenant mode.

Per-request instance — user_id and tenant_id injected via constructor.
Uses MemoryRepo for actual DB operations.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from crab.agents.memory.storage import MemoryStorage, create_empty_memory
from crab_platform.db.repos.memory_repo import MemoryRepo

logger = logging.getLogger(__name__)


class PGMemoryStorage(MemoryStorage):
    """PostgreSQL memory storage provider.

    Unlike FileMemoryStorage (global singleton with mtime cache), this is created
    per-request with an explicit user_id/tenant_id.  The underlying MemoryRepo
    handles all DB access through the provided AsyncSession.

    Because MemoryStorage's ABC methods are synchronous (called from sync middleware
    and the memory updater background thread), we bridge to async via the running
    event loop when available, or asyncio.run() as a fallback.

    Two construction modes:
    1. **Request-scoped** (normal): Pass an existing ``db_session``.
    2. **Background thread** (updater): Pass a ``session_factory`` instead; each
       operation creates, uses, and commits/closes its own session so there is no
       event-loop lifetime mismatch.
    """

    def __init__(
        self,
        db_session: Any = None,  # AsyncSession — typed as Any to avoid import at module level
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        *,
        session_factory: Any | None = None,  # async_sessionmaker
    ) -> None:
        if db_session is not None:
            self._repo = MemoryRepo(db_session)
        else:
            self._repo = None  # Will use session_factory per-operation
        self._session_factory = session_factory
        self._user_id = user_id
        self._tenant_id = tenant_id
        # In-memory cache: agent_name -> memory_data (no mtime, just request-scoped)
        self._cache: dict[str | None, dict[str, Any]] = {}

    @classmethod
    def for_background_thread(
        cls,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> PGMemoryStorage:
        """Create an instance for the memory updater background thread.

        Uses ``create_isolated_session_factory()`` with NullPool to avoid
        sharing loop-bound asyncpg connections with the main FastAPI loop.
        """
        from crab_platform.db import create_isolated_session_factory

        _engine, factory = create_isolated_session_factory()
        return cls(
            user_id=user_id,
            tenant_id=tenant_id,
            session_factory=factory,
        )

    # ------------------------------------------------------------------
    # Async ↔ sync bridge
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro: Any) -> Any:
        """Run an async coroutine from synchronous code.

        Two call-site scenarios:
        1. Background thread (MemoryUpdateQueue) — no running loop → asyncio.run()
        2. Event loop thread (sync ABC called from async context) — must NOT block
           the loop, so we run asyncio.run(coro) in a separate thread via the
           default executor.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're on the event loop thread — delegate to a worker thread so the
            # loop stays free.  The worker thread gets its own fresh loop via
            # asyncio.run().
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=30)
        else:
            return asyncio.run(coro)

    # ------------------------------------------------------------------
    # MemoryStorage ABC implementation
    # ------------------------------------------------------------------

    def load(self, agent_name: str | None = None) -> dict[str, Any]:
        """Load memory data, returning cached value if available."""
        if agent_name in self._cache:
            return self._cache[agent_name]
        return self.reload(agent_name)

    def reload(self, agent_name: str | None = None) -> dict[str, Any]:
        """Force reload memory data from PostgreSQL."""
        try:
            if self._session_factory is not None:
                data = self._run_async(self._load_with_factory(agent_name))
            else:
                data = self._run_async(self._repo.load(self._user_id, agent_name))
        except Exception:
            logger.exception("Failed to load memory from PG for user %s", self._user_id)
            data = None

        if data is None:
            data = create_empty_memory()

        self._cache[agent_name] = data
        return data

    def save(self, memory_data: dict[str, Any], agent_name: str | None = None) -> bool:
        """Save memory data to PostgreSQL."""
        try:
            if self._session_factory is not None:
                self._run_async(self._save_with_factory(memory_data, agent_name))
            else:
                self._run_async(
                    self._repo.save(
                        user_id=self._user_id,
                        tenant_id=self._tenant_id,
                        memory_data=memory_data,
                        agent_name=agent_name,
                    )
                )
            self._cache[agent_name] = memory_data
            logger.info("Memory saved to PG for user %s, agent %s", self._user_id, agent_name)
            return True
        except Exception:
            logger.exception("Failed to save memory to PG for user %s", self._user_id)
            return False

    # ------------------------------------------------------------------
    # Session-factory helpers (for background thread usage)
    # ------------------------------------------------------------------

    async def _load_with_factory(self, agent_name: str | None) -> dict | None:
        """Create a fresh session, load, and close."""
        async with self._session_factory() as session:
            repo = MemoryRepo(session)
            return await repo.load(self._user_id, agent_name)

    async def _save_with_factory(self, memory_data: dict, agent_name: str | None) -> None:
        """Create a fresh session, save, commit, and close."""
        async with self._session_factory() as session:
            repo = MemoryRepo(session)
            await repo.save(
                user_id=self._user_id,
                tenant_id=self._tenant_id,
                memory_data=memory_data,
                agent_name=agent_name,
            )
            await session.commit()


def resolve_pg_memory_storage(user_id: str | uuid.UUID, tenant_id: str | uuid.UUID) -> PGMemoryStorage:
    """Create a background-safe PGMemoryStorage for the given user."""
    uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    tid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    return PGMemoryStorage.for_background_thread(user_id=uid, tenant_id=tid)
