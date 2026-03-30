"""SQLAlchemy async engine and session factory."""

from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from crab_platform.config.platform_config import get_platform_config

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        config = get_platform_config()
        _engine = create_async_engine(
            config.database_url,
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def create_isolated_session_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create a standalone async engine/sessionmaker for background loops.

    Used by components such as the E2B sandbox provider/cleaner that run DB
    work on their own event loop. NullPool avoids sharing loop-bound asyncpg
    connections with the main FastAPI request loop.
    """
    config = get_platform_config()
    engine = create_async_engine(
        config.database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, factory


async def get_db() -> AsyncSession:
    """FastAPI dependency: yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def create_tables():
    """Create all tables. Call once at startup."""
    from crab_platform.db.models import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name != "postgresql":
            return

        # Lightweight schema sync for user-scoped E2B sandbox columns.
        # This project currently relies on create_all() at startup instead of Alembic.
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS sandbox_id VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS sandbox_status VARCHAR(31)"))
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS sandbox_last_seen_at TIMESTAMPTZ")
        )
        # Remove legacy thread-scoped sandbox columns now that E2B is user-scoped.
        await conn.execute(text("ALTER TABLE threads DROP COLUMN IF EXISTS sandbox_id"))
        await conn.execute(text("ALTER TABLE threads DROP COLUMN IF EXISTS sandbox_status"))
        await conn.execute(text("ALTER TABLE threads DROP COLUMN IF EXISTS sandbox_last_seen_at"))
