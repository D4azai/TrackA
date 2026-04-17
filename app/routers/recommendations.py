"""
Recommendation API Endpoints — Production Ready

Endpoints:
- GET /products — Get product recommendations for a seller
- GET /health — Health check for load balancers
- POST /cache/clear — Clear recommendation cache (protect in production)
"""

import logging
import time
import secrets
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.algorithm import RecommendationEngine
from app.services.cache_service import CacheService
from app.config import get_settings
from app.metrics import RECOMMENDATION_CACHE_TOTAL, RECOMMENDATION_COMPUTE_DURATION_SECONDS

logger = logging.getLogger(__name__)
settings = get_settings()


# ==================== SCHEMAS ====================

class RecommendationSource(BaseModel):
    """Breakdown of recommendation score by signal."""
    popularity: float = Field(..., ge=0, description="Popularity signal contribution")
    history: float = Field(..., ge=0, description="History signal contribution")
    recency: float = Field(..., ge=0, description="Recency signal contribution")
    newness: float = Field(..., ge=0, description="Newness signal contribution")
    engagement: float = Field(..., ge=0, description="Engagement signal contribution")


class Recommendation(BaseModel):
    """Single product recommendation."""
    product_id: int
    score: float = Field(..., ge=0, le=100, description="Final recommendation score")
    rank: int = Field(..., ge=1, description="Ranking position")
    sources: RecommendationSource = Field(..., description="Score breakdown by signal")


class RecommendationResponse(BaseModel):
    """API response with recommendations."""
    seller_id: str
    recommendations: List[Recommendation]
    count: int = Field(..., ge=0, description="Number of recommendations")
    cache_hit: bool = Field(default=False, description="Whether result came from cache")
    elapsed_ms: Optional[float] = Field(None, description="Time to compute (milliseconds)")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    version: str


# ==================== ROUTER ====================

router = APIRouter()
cache_service = CacheService()


def get_recommendation_engine(db: Session = Depends(get_db)) -> RecommendationEngine:
    """Dependency injection for recommendation engine."""
    return RecommendationEngine(db)


def verify_admin_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Protect admin endpoints with API key.
    - In production, ADMIN_API_KEY must be configured and provided.
    - In non-production, if ADMIN_API_KEY is configured it is enforced.
    """
    configured_key = settings.admin_api_key

    if settings.is_production and not configured_key:
        logger.error("Admin endpoint blocked: ADMIN_API_KEY missing in production")
        raise HTTPException(
            status_code=503,
            detail="Admin endpoint disabled: ADMIN_API_KEY is not configured"
        )

    if configured_key and not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if configured_key and not secrets.compare_digest(x_api_key, configured_key):
        raise HTTPException(status_code=403, detail="Invalid API key")


# ==================== ENDPOINTS ====================

@router.get(
    "/products",
    response_model=RecommendationResponse,
    summary="Get product recommendations for a seller",
    tags=["Recommendations"]
)
async def get_recommendations(
    seller_id: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="Seller ID requesting recommendations"
    ),
    limit: int = Query(
        30,
        ge=1,
        le=100,
        description="Number of recommendations (1-100)"
    ),
    engine: RecommendationEngine = Depends(get_recommendation_engine)
) -> RecommendationResponse:
    """
    Get intelligent product recommendations for a seller.

    Algorithm: Weighted ensemble of 5 signals
    - Popularity (25%): Global trending products
    - History (35%): Seller's past orders
    - Recency (20%): When seller last ordered (seller-specific)
    - Newness (15%): Product age
    - Engagement (5%): Likes and comments

    Example:
    ```
    GET /api/recommend/products?seller_id=seller123&limit=30
    ```
    """
    try:
        start_time = time.time()

        # Check cache first
        cache_hit = False
        if settings.cache_enabled:
            cached = cache_service.get_recommendations(seller_id)
            if cached:
                cache_hit = True
                RECOMMENDATION_CACHE_TOTAL.labels(result="hit").inc()
                logger.info(f"Cache hit for seller {seller_id}")
                limited = cached[:limit]
                elapsed = time.time() - start_time
                RECOMMENDATION_COMPUTE_DURATION_SECONDS.labels(cache_hit="true").observe(elapsed)
                return RecommendationResponse(
                    seller_id=seller_id,
                    recommendations=[Recommendation(**r) for r in limited],
                    count=len(limited),
                    cache_hit=True,
                    elapsed_ms=round(elapsed * 1000, 2)
                )

            RECOMMENDATION_CACHE_TOTAL.labels(result="miss").inc()

        # Compute recommendations
        recommendations = engine.compute_recommendations(seller_id, limit)

        # Build response
        elapsed = time.time() - start_time
        RECOMMENDATION_COMPUTE_DURATION_SECONDS.labels(cache_hit="false").observe(elapsed)
        response = RecommendationResponse(
            seller_id=seller_id,
            recommendations=[
                Recommendation(**r) if isinstance(r, dict) else r
                for r in recommendations
            ],
            count=len(recommendations),
            cache_hit=False,
            elapsed_ms=round(elapsed * 1000, 2)
        )

        # Cache result
        if settings.cache_enabled:
            cache_service.set_recommendations(seller_id, recommendations, settings.cache_ttl_seconds)

        return response

    except ValueError as e:
        logger.warning(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting recommendations: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to compute recommendations"
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    tags=["Health"]
)
async def health_check() -> HealthResponse:
    """
    Simple health check endpoint for load balancers.
    No authentication required.
    Returns 200 if service is operational.
    """
    return HealthResponse(
        status="healthy",
        service="recommendation-engine",
        version="2.0.0"
    )


@router.post(
    "/cache/clear",
    summary="Clear recommendation cache",
    tags=["Admin"]
)
async def clear_cache(
    seller_id: Optional[str] = Query(
        None,
        description="Optional: clear only specific seller's cache"
    ),
    _: None = Depends(verify_admin_api_key)
) -> dict:
    """
    Clear cached recommendations.

    SECURITY: Protected by X-API-Key (ADMIN_API_KEY).

    Parameters:
    - seller_id: If provided, clears only that seller's cache.
                If not provided, clears all cached recommendations.
    """
    try:
        if seller_id:
            cache_service.delete(seller_id)
            logger.info(f"Cleared cache for seller {seller_id}")
            return {"status": "cleared", "seller_id": seller_id}
        else:
            cache_service.clear_all()
            logger.info("Cleared all caches")
            return {"status": "cleared", "scope": "all"}
    except Exception as e:
        logger.error(f"Error clearing cache: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")
