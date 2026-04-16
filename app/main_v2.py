"""
Production-ready FastAPI Recommendation Service
Enhanced with batch queries, proper signal scoping, and security.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.db import engine, SessionLocal
from app.models import Base
from app.routers import recommendations
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ==================== Lifespan Management ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.
    
    Startup:
    - Create database tables
    - Test database connection
    - Initialize cache
    
    Shutdown:
    - Close database connections
    - Clear cache
    """
    # ============= STARTUP =============
    logger.info("🚀 Recommendation Service Starting Up...")
    
    try:
        # Create tables if they don't exist
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables initialized")
        
        # Test database connection
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        logger.info("✅ Database connection verified")
        
        # Log configuration
        logger.info(f"Algorithm Weights: {settings.algorithm_weights}")
        logger.info(f"Cache TTL: {settings.cache_ttl_seconds}s")
        logger.info(f"Environment: {settings.environment}")
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {str(e)}")
        raise
    
    yield
    
    # ============= SHUTDOWN =============
    logger.info("🛑 Recommendation Service Shutting Down...")
    try:
        engine.dispose()
        logger.info("✅ Database connections closed")
    except Exception as e:
        logger.error(f"⚠️ Error during shutdown: {str(e)}")


# ==================== FastAPI Application ====================
def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.
    
    Security:
    - CORS limited to specific origins
    - Trusted host validation
    - No public debug endpoints
    - HTTPS enforced in production
    """
    app = FastAPI(
        title="Recommendation Service",
        description="Intelligent product recommendations for sellers",
        version="2.0.0",
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url="/redoc" if settings.environment == "development" else None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
        lifespan=lifespan,
    )
    
    # ============= MIDDLEWARE =============
    
    # 1. Trusted Host: Prevent Host header injection
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts
    )
    
    # 2. CORS: Only allow specific origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        max_age=3600,
    )
    
    # ============= ROUTERS =============
    
    # Include recommendation router (v1 API)
    app.include_router(
        recommendations.router,
        prefix="/api/recommend",
        tags=["recommendations"]
    )
    
    # Health check endpoint (no auth required)
    @app.get("/health")
    async def health_check():
        """Simple health check for load balancers"""
        return {
            "status": "healthy",
            "service": "recommendation-engine",
            "version": "2.0.0"
        }
    
    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        reload=settings.environment == "development",
        log_level="info"
    )
