"""
Recommendation API Endpoints - Production Ready

Security:
- No authentication required for now (can be added)
- No public debug endpoints
- Proper input validation
- Error handling with user-friendly messages
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.db import get_db
from app.services.algorithm_v2 import RecommendationEngine
from app.services.cache_service import CacheService
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ==================== SCHEMAS ====================

class RecommendationSource(BaseModel):
    """Breakdown of recommendation score by signal"""
    popularity: float = Field(..., ge=0, le=100, description="Global popularity signal")
    history: float = Field(..., ge=0, le=100, description="Seller history signal")
    recency: float = Field(..., ge=0, le=100, description="Seller recency signal")
    newness: float = Field(..., ge=0, le=100, description="Product newness signal")
    engagement: float = Field(..., ge=0, le=100, description="Engagement signal")


class Recommendation(BaseModel):
    """Single product recommendation"""
    product_id: int
    score: float = Field(..., ge=0, le=100, description="Final recommendation score")
    rank: int = Field(..., ge=1, description="Ranking position")
    sources: RecommendationSource = Field(..., description="Score breakdown by signal")


class RecommendationResponse(BaseModel):
    """API response with recommendations"""
    seller_id: str
    recommendations: List[Recommendation]
    count: int = Field(..., ge=0, description="Number of recommendations")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    service: str
    version: str


# ==================== ROUTER ====================

router = APIRouter()
cache_service = CacheService()


def get_recommendation_engine(db: Session = Depends(get_db)) -> RecommendationEngine:
    """Dependency injection for recommendation engine"""
    return RecommendationEngine(db)


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
    
    Response: 30 recommended products with scores and signal breakdown
    """
    
    try:
        # Check cache first
        if settings.cache_enabled:
            cached = cache_service.get_recommendations(seller_id)
            if cached:
                logger.info(f"Cache hit for seller {seller_id}")
                # Filter to limit and return
                limited = cached[:limit] if limit else cached
                return RecommendationResponse(
                    seller_id=seller_id,
                    recommendations=[Recommendation(**r) for r in limited],
                    count=len(limited)
                )
        
        # Compute recommendations
        recommendations = engine.compute_recommendations(seller_id, limit)
        
        # Build response
        response = RecommendationResponse(
            seller_id=seller_id,
            recommendations=[Recommendation(**r) if isinstance(r, dict) else r for r in recommendations],
            count=len(recommendations)
        )
        
        # Cache result
        if settings.cache_enabled:
            recommendations_data = [r.dict() if hasattr(r, 'dict') else r for r in recommendations]
            cache_service.set_recommendations(seller_id, recommendations_data, settings.cache_ttl_seconds)
        
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
    )
) -> dict:
    """
    Clear cached recommendations.
    
    SECURITY: This endpoint should be protected with API key or JWT in production!
    
    Parameters:
    - seller_id: If provided, clears only that seller's cache
                If not provided, clears all cached recommendations
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


# ==================== REMOVED ENDPOINTS ====================
# The following insecure endpoints have been removed:
#
# ❌ GET /debug/seller/{seller_id}
#    Reason: Exposed complete seller order history and preferences
#    Severity: SECURITY RISK - Privacy leak
#
# ❌ GET /products/{product_id}/score
#    Reason: Exposed internal scoring logic and data
#    Severity: MEDIUM RISK - Could be used to game recommendations
#
# ❌ POST /batch/compute
#    Reason: Allowed batch requests without rate limiting
#    Severity: MEDIUM RISK - DOS vulnerability
#
# These should only be available in development environment with proper auth.
# If you need debugging endpoints, enable them only when environment=="development"
# and add JWT authentication.


# ==================== OPTIONAL: DEVELOPMENT-ONLY ENDPOINTS ====================
# Uncomment if you need debugging in development environment
# Add this to your FastAPI app initialization:
#
# if settings.is_development:
#     from app.routers import debug_router
#     app.include_router(debug_router, prefix="/api/recommend/debug", tags=["Debug"])
