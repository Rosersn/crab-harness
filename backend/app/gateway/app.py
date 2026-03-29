import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.config import get_gateway_config
from app.gateway.middleware import AccessLogMiddleware, AuthEnforcementMiddleware, RequestIdMiddleware
from app.gateway.routers import (
    agents,
    artifacts,
    langgraph_compat,
    mcp,
    memory,
    models,
    skills,
    suggestions,
    uploads,
)
from app.gateway.routers import auth as auth_router
from crab_platform.config.platform_config import get_platform_config
from crab_platform.db import create_tables
from crab_platform.db.repos.thread_repo import RunRepo
from crab_platform.storage.pg_memory import resolve_pg_memory_storage
from deerflow.agents.memory.updater import set_memory_storage_resolver
from deerflow.config.app_config import get_app_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""

    # Load configs
    try:
        get_app_config()
        logger.info("Configuration loaded successfully")
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e

    config = get_gateway_config()
    platform_config = get_platform_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # Initialize DB tables
    try:
        await create_tables()
        logger.info("Database tables initialized")
    except Exception:
        logger.exception("Failed to initialize database tables")
        raise

    set_memory_storage_resolver(resolve_pg_memory_storage)
    logger.info("Registered user-scoped memory storage resolver")

    # Crash recovery: mark orphaned runs as failed
    try:
        from crab_platform.db import get_session_factory

        async with get_session_factory()() as session:
            repo = RunRepo(session)
            count = await repo.mark_orphaned_runs_failed(platform_config.instance_id)
            if count:
                logger.info("Crash recovery: marked %d orphaned runs as failed", count)
            await session.commit()
    except Exception:
        logger.exception("Crash recovery failed")

    # Start SandboxCleaner background thread (if E2B sandbox is configured)
    try:
        sandbox_config = get_app_config().sandbox
        provider_use = getattr(sandbox_config, "use", "")
        if "E2BSandboxProvider" in str(provider_use):
            from crab_platform.sandbox.cleaner import SandboxCleaner

            e2b_api_key = getattr(sandbox_config, "e2b_api_key", None) or None
            e2b_api_url = getattr(sandbox_config, "e2b_api_url", None) or None
            cleaner = SandboxCleaner(
                e2b_api_key=e2b_api_key,
                e2b_api_url=e2b_api_url,
            )
            cleaner.start()
            app._sandbox_cleaner = cleaner  # type: ignore[attr-defined]
            logger.info("SandboxCleaner started")
    except Exception:
        logger.debug("SandboxCleaner startup skipped or failed", exc_info=True)

    yield

    # Shutdown: clean up sandbox provider and cleaner
    try:
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        provider = get_sandbox_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
            logger.info("Sandbox provider shut down")
    except Exception:
        logger.debug("Sandbox provider shutdown skipped or failed", exc_info=True)

    try:
        if hasattr(app, "_sandbox_cleaner"):
            app._sandbox_cleaner.stop()
            logger.info("SandboxCleaner stopped")
    except Exception:
        logger.debug("SandboxCleaner stop skipped or failed", exc_info=True)

    set_memory_storage_resolver(None)

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    config = get_gateway_config()

    app = FastAPI(
        title="Crab Harness API Gateway",
        description="Multi-tenant AI Agent API Gateway",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "auth", "description": "Authentication: register, login, refresh, me"},
            {"name": "models", "description": "Query available AI models and configurations"},
            {"name": "mcp", "description": "Manage MCP server configurations"},
            {"name": "memory", "description": "Access and manage user memory data"},
            {"name": "skills", "description": "Manage skills and configurations"},
            {"name": "artifacts", "description": "Access thread artifacts and generated files"},
            {"name": "uploads", "description": "Upload and manage user files for threads"},
            {"name": "threads", "description": "Thread CRUD and lifecycle management"},
            {"name": "agents", "description": "Create and manage custom agents"},
            {"name": "suggestions", "description": "Generate follow-up question suggestions"},
            {"name": "health", "description": "Health check and system status"},
        ],
    )

    # Middleware (outermost first)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(AuthEnforcementMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Location"],
    )

    # Auth routes (public, no auth required)
    app.include_router(auth_router.router)

    # LangGraph SDK-compatible endpoints
    app.include_router(langgraph_compat.router)

    # Protected routes
    app.include_router(models.router)
    app.include_router(mcp.router)
    app.include_router(memory.router)
    app.include_router(skills.router)
    app.include_router(artifacts.router)
    app.include_router(uploads.router)
    app.include_router(agents.router)
    app.include_router(suggestions.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """Health check endpoint."""
        return {"status": "healthy", "service": "crab-harness-gateway"}

    return app


# Create app instance for uvicorn
app = create_app()
