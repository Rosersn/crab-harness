"""SQLAlchemy async engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
