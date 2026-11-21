"""FastAPI application initialization with CORS middleware and route registration."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rhp_migrate.server.routes.analyze import router as analyze_router
from rhp_migrate.server.routes.migrate import router as migrate_router
from rhp_migrate.server.routes.rules import router as rules_router


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
    )

    # Configure CORS middleware for broad frontend compatibility
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers under /api prefix
    app.include_router(analyze_router, prefix="/api", tags=["Analysis"])
    app.include_router(migrate_router, prefix="/api", tags=["Migration"])
    app.include_router(rules_router, prefix="/api", tags=["Rules & Memory"])

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
