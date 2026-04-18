"""
Recommendation API Endpoints — Production Ready

Endpoints:
- GET  /products      — Get personalised product recommendations for a seller
- GET  /health        — Health check for load balancers
- POST /cache/clear   — Clear recommendation cache (protected by X-API-Key)
"""

import logging
import time
import secrets
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.algorithm import RecommendationEngine
from app.services.cache_service import CacheService
from app.config import get_settings
from app.meta import VERSION

try:
    from app.metrics import RECOMMENDATION_CACHE_TOTAL, RECOMMENDATION_COMPUTE_DURATION_SECONDS
    _metrics_available = True
except ImportError:
    _metrics_available = False

logger = logging.getLogger(__name__)
settings = get_settings()

# ==================== SCHEMAS ====================

class RecommendationSource(BaseModel):
    """Score breakdown per signal."""
    popularity: float = Field(..., ge=0, description="Popularity signal contribution")
    history:    float = Field(..., ge=0, description="History signal contribution")
    recency:    float = Field(..., ge=0, description="Recency signal contribution")
    newness:    float = Field(..., ge=0, description="Newness signal contribution")
    engagement: float = Field(..., ge=0, description="Engagement signal contribution")


class Recommendation(BaseModel):
    """Single product recommendation."""
    product_id:      int
    score:           float = Field(..., ge=0, le=100, description="Final recommendation score (0-100)")
    rank:            int   = Field(..., ge=1, description="Ranking position (1 = best)")
    is_personalized: bool  = Field(default=False, description="True if seller order history influenced this")
    sources:         RecommendationSource = Field(..., description="Score breakdown by signal")


class RecommendationResponse(BaseModel):
    """Full API response."""
    seller_id:       str
    recommendations: List[Recommendation]
    count:           int           = Field(..., ge=0)
    cache_hit:       bool          = Field(default=False)
    personalized:    int           = Field(default=0, description="Number of personalized results")
    elapsed_ms:      Optional[float] = Field(None, description="Compute time in ms")


class HealthResponse(BaseModel):
    status:  str
    service: str
    version: str
    cache:   str = Field(default="unknown")


# ==================== ROUTER ====================

router = APIRouter()
cache_service = CacheService()


def get_recommendation_engine(db: Session = Depends(get_db)) -> RecommendationEngine:
    """Dependency injection for recommendation engine."""
    return RecommendationEngine(db)


def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> None:
    """
    Validate admin API key for protected endpoints.
    If ADMIN_API_KEY is not configured, this endpoint is disabled.
    """
    configured_key = settings.admin_api_key
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key not configured. Set ADMIN_API_KEY in environment."
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header."
        )


# ==================== ENDPOINTS ====================

@router.get(
    "/products",
    response_model=RecommendationResponse,
    summary="Get personalised product recommendations for a seller",
    tags=["Recommendations"],
)
async def get_recommendations(
    seller_id: str = Query(
        ...,
        min_length=1,
        max_length=128,
        description="Seller ID requesting recommendations",
    ),
    limit: int = Query(
        30,
        ge=1,
        le=100,
        description="Number of recommendations to return (1-100)",
    ),
    engine: RecommendationEngine = Depends(get_recommendation_engine),
) -> RecommendationResponse:
    """
    Get intelligent product recommendations for a seller.

    **Algorithm:** Weighted ensemble of 5 signals
    - **Popularity** (25%): Globally trending products (last 90 days)
    - **History** (35%): Seller's past orders — with category-affinity fallback
    - **Recency** (20%): When this seller last ordered each product
    - **Newness** (15%): Product age (newer = higher score)
    - **Engagement** (5%): Likes and comments on the product

    **Category Affinity:** If a seller has no order history for a product, but
    frequently buys from its category, the product still gets a boosted ranking.

    **Caching:** Results are cached per seller for 1 hour. Pass `?limit=` to
    slice the cached list without recomputing.

    Example:
    ```
    GET /api/recommend/products?seller_id=abc123&limit=30
    ```
    """
    try:
        t_start = time.time()

        # ---- Cache check ----
        if settings.cache_enabled:
            cached = cache_service.get_recommendations(seller_id)
            if cached is not None:
                if _metrics_available:
                    RECOMMENDATION_CACHE_TOTAL.labels(result="hit").inc()
                limited = cached[:limit]
                elapsed = (time.time() - t_start) * 1000
                return RecommendationResponse(
                    seller_id=seller_id,
                    recommendations=[Recommendation(**r) for r in limited],
                    count=len(limited),
                    cache_hit=True,
                    personalized=sum(1 for r in limited if r.get("is_personalized")),
                    elapsed_ms=round(elapsed, 2),
                )
            if _metrics_available:
                RECOMMENDATION_CACHE_TOTAL.labels(result="miss").inc()

        # ---- Compute ----
        raw = engine.compute_recommendations(seller_id, limit)

        elapsed = (time.time() - t_start) * 1000
        recommendations = [
            Recommendation(**r) if isinstance(r, dict) else r
            for r in raw
        ]

        response = RecommendationResponse(
            seller_id=seller_id,
            recommendations=recommendations,
            count=len(recommendations),
            cache_hit=False,
            personalized=sum(1 for r in recommendations if r.is_personalized),
            elapsed_ms=round(elapsed, 2),
        )

        # ---- Store in cache ----
        if settings.cache_enabled and raw:
            cache_service.set_recommendations(seller_id, raw, settings.cache_ttl_seconds)

        return response

    except ValueError as e:
        logger.warning(f"Validation error for seller {seller_id!r}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error computing recommendations for seller {seller_id!r}: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Recommendation engine temporarily unavailable. Please retry.",
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    tags=["Health"],
)
async def health_check() -> HealthResponse:
    """
    Lightweight health check for load balancers and uptime monitors.
    Always returns 200 if the service process is alive.
    """
    cache_status = "ok" if cache_service.is_healthy() else "unavailable"
    return HealthResponse(
        status="healthy",
        service="recommendation-engine",
        version=VERSION,
        cache=cache_status,
    )


@router.post(
    "/cache/clear",
    summary="Clear recommendation cache",
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
)
async def clear_cache(
    seller_id: Optional[str] = Query(
        None,
        description="If provided, clears only this seller's cache. Otherwise clears all.",
    ),
) -> dict:
    """
    Clear cached recommendations.

    **Authentication:** Requires `X-API-Key: <ADMIN_API_KEY>` header.

    - With `seller_id`: clears only that seller's cache.
    - Without `seller_id`: clears ALL cached recommendations.
    """
    try:
        if seller_id:
            cache_service.delete(seller_id)
            logger.info(f"Admin: cleared cache for seller {seller_id!r}")
            return {"status": "cleared", "seller_id": seller_id}
        else:
            cache_service.clear_all()
            logger.warning("Admin: cleared ALL recommendation caches")
            return {"status": "cleared", "scope": "all"}
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")
