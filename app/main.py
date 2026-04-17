"""
FastAPI application — Recommendation Service
Main entry point. Production-ready with security middleware and proper lifecycle.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
import logging

from app.routers import recommendations
from app.config import get_settings, setup_logging
from app.db import engine, test_connection

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan: startup and shutdown.

    Startup: test DB connection, log config.
    Shutdown: dispose DB engine.
    """
    logger.info("🚀 Recommendation service starting up...")

    # Test database connection
    if test_connection():
        logger.info("✅ Database connection verified")
    else:
        logger.warning("⚠️ Database connection failed — service may not work correctly")

    # Log configuration (no credentials)
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Algorithm weights: {settings.algorithm_weights}")
    logger.info(f"Cache enabled: {settings.cache_enabled}, TTL: {settings.cache_ttl_seconds}s")

    yield

    # Shutdown
    logger.info("🛑 Recommendation service shutting down...")
    try:
        engine.dispose()
        logger.info("✅ Database connections closed")
    except Exception as e:
        logger.error(f"⚠️ Error during shutdown: {str(e)}")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""

    app = FastAPI(
        title="Recommendation Service",
        description="Intelligent product recommendations for sellers",
        version="2.0.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    # ============= MIDDLEWARE =============

    # Trusted Host: Prevent Host header injection
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts
    )

    # CORS: Configurable origins (not "*")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        max_age=3600,
    )

    # ============= ROUTERS =============
    app.include_router(
        recommendations.router,
        prefix="/api/recommend",
        tags=["recommendations"]
    )

    # Root endpoint
    @app.get("/")
    async def root():
        """Root endpoint — service info."""
        return {
            "service": "Recommendation Service",
            "version": "2.0.0",
            "endpoints": {
                "recommendations": "GET /api/recommend/products?seller_id=X&limit=20",
                "health": "GET /api/recommend/health",
                "cache_clear": "POST /api/recommend/cache/clear"
            },
            "docs": "/docs" if settings.is_development else "disabled in production"
        }

    return app


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.service_port,
        reload=settings.is_development,
        log_level="info"
    )
