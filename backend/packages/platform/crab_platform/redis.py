"""Redis-based distributed locks and utilities for thread concurrency control."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis

from crab_platform.config.platform_config import get_platform_config

logger = logging.getLogger(__name__)

_redis_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Get or create a shared async Redis connection."""
    global _redis_pool
    if _redis_pool is None:
        config = get_platform_config()
        _redis_pool = aioredis.from_url(
            config.redis_url,
            decode_responses=True,
        )
    return _redis_pool


@dataclass
class ThreadLock:
    """Result of attempting to acquire a thread-level lock."""

    acquired: bool
    thread_id: str
    run_id: str
    owner_token: str

    async def release(self) -> None:
        """Release the lock if we still own it (CAS via Lua script)."""
        if not self.acquired:
            return
        r = get_redis()
        key = f"thread_run:{self.thread_id}"
        # Only delete if we still own the lock
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        await r.eval(script, 1, key, self.owner_token)

    async def extend(self, ttl_seconds: int = 600) -> bool:
        """Extend the lock TTL (heartbeat). Returns True if still owned."""
        r = get_redis()
        key = f"thread_run:{self.thread_id}"
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            redis.call("expire", KEYS[1], ARGV[2])
            return 1
        end
        return 0
        """
        result = await r.eval(script, 1, key, self.owner_token, str(ttl_seconds))
        return result == 1


async def acquire_thread_lock(
    thread_id: str | uuid.UUID,
    run_id: str | uuid.UUID,
    ttl_seconds: int = 600,
) -> ThreadLock:
    """Attempt to acquire an exclusive lock for a thread.

    Uses Redis SET NX with TTL to ensure only one run executes
    per thread at a time.

    Args:
        thread_id: The thread to lock.
        run_id: The run that wants to acquire the lock.
        ttl_seconds: Lock expiration (default 10 minutes).

    Returns:
        ThreadLock with acquired=True if lock was obtained.
    """
    r = get_redis()
    key = f"thread_run:{str(thread_id)}"
    owner_token = f"{run_id}:{uuid.uuid4().hex[:8]}"

    acquired = await r.set(key, owner_token, nx=True, ex=ttl_seconds)

    lock = ThreadLock(
        acquired=bool(acquired),
        thread_id=str(thread_id),
        run_id=str(run_id),
        owner_token=owner_token,
    )

    if acquired:
        logger.debug("Thread lock acquired: thread=%s run=%s", thread_id, run_id)
    else:
        current = await r.get(key)
        logger.debug("Thread lock busy: thread=%s run=%s holder=%s", thread_id, run_id, current)

    return lock


async def release_thread_lock(thread_id: str | uuid.UUID, run_id: str | uuid.UUID) -> None:
    """Forcibly release a thread lock (admin/crash recovery use only).

    Prefer ThreadLock.release() for normal flow.
    """
    r = get_redis()
    key = f"thread_run:{str(thread_id)}"
    await r.delete(key)
    logger.info("Thread lock force-released: thread=%s run=%s", thread_id, run_id)


async def request_run_cancellation(run_id: str | uuid.UUID, ttl_seconds: int = 3600) -> None:
    """Publish a distributed cancellation flag for a run."""
    r = get_redis()
    await r.set(f"run_cancel:{str(run_id)}", "1", ex=ttl_seconds)


async def is_run_cancellation_requested(run_id: str | uuid.UUID) -> bool:
    """Check whether cancellation has been requested for a run."""
    r = get_redis()
    return bool(await r.exists(f"run_cancel:{str(run_id)}"))


async def clear_run_cancellation(run_id: str | uuid.UUID) -> None:
    """Clear a distributed cancellation flag after the run exits."""
    r = get_redis()
    await r.delete(f"run_cancel:{str(run_id)}")
