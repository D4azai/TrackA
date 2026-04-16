"""
FastAPI application initialization
Main entry point for the recommendation service
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from .routers import recommendations_v2
from .config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown"""
    logger.info("🚀 Recommendation service starting up...")
    logger.info(f"Database: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'configured'}")
    logger.info(f"Redis: {settings.redis_url}")
    logger.info(f"Algorithm weights: Popularity={settings.weight_popularity}, History={settings.weight_history}, "
                f"Engagement={settings.weight_engagement}, Recency={settings.weight_recency}, Newness={settings.weight_newness}")

    yield

    logger.info("🛑 Recommendation service shutting down...")


def create_app() -> FastAPI:
    """Create and configure FastAPI application"""

    app = FastAPI(
        title="Maroc Affiliate Recommendation Service",
        description="FastAPI microservice for product recommendations",
        version="1.0.0",
        lifespan=lifespan
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this to your Next.js domain
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(recommendations_v2.router, prefix="/api/recommend", tags=["recommendations"])

    # Root endpoint
    @app.get("/")
    async def root():
        """Root endpoint - service info"""
        return {
            "service": "Maroc Affiliate Recommendation Service",
            "version": "1.0.0",
            "endpoints": {
                "recommendations": "GET /api/recommend/products?seller_id=X&limit=20",
                "health": "GET /api/recommend/health",
                "cache_invalidation": "POST /api/recommend/invalidate"
            },
            "docs": "/docs"
        }

    return app


# Create the application instance
app = create_app()
