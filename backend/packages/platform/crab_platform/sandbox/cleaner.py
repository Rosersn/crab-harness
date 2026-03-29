"""Background cleaner for stale E2B sandboxes.

Runs as a daemon thread, periodically scanning PG for sandboxes whose
``sandbox_last_seen_at`` exceeds the configured TTL, and terminates them
via the E2B SDK.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_TTL_HOURS = 24
_DEFAULT_CHECK_INTERVAL_MINUTES = 30


class SandboxCleaner:
    """Background thread that periodically terminates stale E2B sandboxes.

    Scans the ``threads`` table for rows with ``sandbox_status='active'`` (or
    ``'paused'``) whose ``sandbox_last_seen_at`` is older than *ttl_hours*.
    For each, it connects to the E2B sandbox and kills it, then clears the PG
    fields.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        ttl_hours: int = _DEFAULT_TTL_HOURS,
        check_interval_minutes: int = _DEFAULT_CHECK_INTERVAL_MINUTES,
        e2b_api_key: str | None = None,
        e2b_api_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._session_engine: AsyncEngine | None = None
        self._ttl_hours = ttl_hours
        self._check_interval = check_interval_minutes * 60  # seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._e2b_api_key = e2b_api_key
        self._e2b_api_url = e2b_api_url

    def _get_session_factory(self):
        if self._session_factory is None:
            from crab_platform.db import create_isolated_session_factory
            self._session_engine, self._session_factory = create_isolated_session_factory()
        return self._session_factory

    def start(self) -> None:
        """Start the cleanup daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="e2b-sandbox-cleaner",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "E2B SandboxCleaner started (ttl=%dh, interval=%dm)",
            self._ttl_hours,
            self._check_interval // 60,
        )

    def stop(self) -> None:
        """Signal the cleaner to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._session_factory = None
        self._session_engine = None
        logger.info("E2B SandboxCleaner stopped")

    def _run_loop(self) -> None:
        """Main loop: sleep, then clean."""
        runner = asyncio.Runner()
        try:
            while not self._stop_event.wait(timeout=self._check_interval):
                try:
                    runner.run(self._cleanup_stale_sandboxes())
                except Exception:
                    logger.error("SandboxCleaner cycle failed", exc_info=True)
        finally:
            runner.close()

    async def _cleanup_stale_sandboxes(self) -> None:
        """Find and terminate sandboxes that haven't been active within the TTL."""
        from crab_platform.db.models import Thread

        cutoff = datetime.now(UTC) - timedelta(hours=self._ttl_hours)
        factory = self._get_session_factory()

        async with factory() as db:
            # Find stale sandboxes
            result = await db.execute(
                select(Thread.id, Thread.sandbox_id, Thread.sandbox_status)
                .where(
                    Thread.sandbox_id.is_not(None),
                    Thread.sandbox_status.in_(["active", "paused"]),
                    Thread.sandbox_last_seen_at < cutoff,
                )
            )
            stale = result.all()

            if not stale:
                return

            logger.info("Found %d stale E2B sandboxes to terminate", len(stale))

            for thread_id, sandbox_id, status in stale:
                await self._terminate_sandbox(db, thread_id, sandbox_id)

            await db.commit()

    async def _terminate_sandbox(
        self,
        db: AsyncSession,
        thread_id: uuid.UUID,
        sandbox_id: str,
    ) -> None:
        """Kill an E2B sandbox and clear PG fields."""
        from crab_platform.db.models import Thread

        try:
            from e2b import Sandbox as E2BSdkSandbox
            opts: dict = {}
            if self._e2b_api_key:
                opts["api_key"] = self._e2b_api_key
            if self._e2b_api_url:
                opts["api_url"] = self._e2b_api_url
            sbx = E2BSdkSandbox.connect(sandbox_id, **opts)
            sbx.kill()
            logger.info("Terminated stale E2B sandbox %s (thread %s)", sandbox_id, thread_id)
        except Exception:
            # Sandbox may already be dead — that's fine, just clear PG
            logger.debug(
                "Could not connect/kill E2B sandbox %s (may already be gone)",
                sandbox_id,
                exc_info=True,
            )

        # Clear PG sandbox fields
        await db.execute(
            update(Thread)
            .where(Thread.id == thread_id)
            .values(
                sandbox_id=None,
                sandbox_status="terminated",
                sandbox_last_seen_at=None,
            )
        )
