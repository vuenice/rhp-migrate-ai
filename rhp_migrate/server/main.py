"""FastAPI application initialization with CORS middleware and route registration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import sys
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Initialize server-wide logging format and level
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("rhp_migrate.server")

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
    logger.info("Initializing rhp-migrate-ai API server lifespan...")
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
        logger.info("Shutting down rhp-migrate-ai API server lifespan...")
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

    # HTTP Request & Performance Logging Middleware
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        start_time = time.time()
        client_host = request.client.host if request.client else "unknown"
        logger.info("--> %s %s from %s", request.method, request.url.path, client_host)
        try:
            response = await call_next(request)
            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(
                "<-- %s %s [%s] completed in %.1fms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            return response
        except Exception as exc:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.exception(
                "<-- %s %s [ERROR] failed after %.1fms: %s",
                request.method,
                request.url.path,
                elapsed_ms,
                exc,
            )
            raise

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
