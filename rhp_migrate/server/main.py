"""FastAPI application initialization with CORS middleware and route registration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rhp_migrate.server.routes.analyze import router as analyze_router
from rhp_migrate.server.routes.migrate import router as migrate_router
from rhp_migrate.server.routes.rules import router as rules_router
from rhp_migrate.server.workspace import (
    periodic_workspace_cleanup_task,
    router as workspace_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context for background tasks."""
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(
        periodic_workspace_cleanup_task(
            interval_seconds=1800,
            max_age_seconds=7200,
            stop_event=stop_event,
        )
    )
    try:
        yield
    finally:
        stop_event.set()
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (asyncio.CancelledError, Exception):
            pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="rhp-migrate-ai API",
        description=(
            "Modern REST and SSE Streaming API for rhp-migrate-ai. "
            "Features AST readiness analysis, incremental code migration to strictly-typed RHP, "
            "compiler verification feedback loops, continuous improvement critic, "
            "and BYOK (Bring-Your-Own-Key) multi-provider inference via LiteLLM."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Configure CORS middleware (configurable via CORS_ORIGINS or RHP_CORS_ORIGINS)
    cors_env = os.environ.get("CORS_ORIGINS") or os.environ.get("RHP_CORS_ORIGINS", "*")
    if cors_env.strip() == "*":
        allow_origins = ["*"]
    else:
        allow_origins = [orig.strip() for orig in cors_env.split(",") if orig.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers under /api prefix
    app.include_router(analyze_router, prefix="/api", tags=["Analysis"])
    app.include_router(migrate_router, prefix="/api", tags=["Migration"])
    app.include_router(rules_router, prefix="/api", tags=["Rules & Memory"])
    app.include_router(workspace_router, prefix="/api", tags=["Workspace"])


    @app.get("/", tags=["Health"])
    async def root():
        """Root status endpoint."""
        return {
            "name": "rhp-migrate-ai API",
            "version": "0.1.0",
            "status": "running",
            "documentation": "/docs",
        }

    @app.get("/api/health", tags=["Health"])
    async def health_check():
        """System health and liveness probe."""
        return {
            "status": "healthy",
            "service": "rhp-migrate-ai",
            "version": "0.1.0",
        }

    return app


app = create_app()


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Start the Uvicorn ASGI server."""
    import uvicorn

    uvicorn.run("rhp_migrate.server.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    run()
