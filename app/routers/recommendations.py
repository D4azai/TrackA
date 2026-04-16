"""
FastAPI routers for recommendations API
Exposed endpoints for Next.js integration
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
import time

from ..db import get_db
from ..services.algorithm import RecommendationEngine
from ..services.cache_service import CacheService
from ..schemas import RecommendationRequest, RecommendationResponse, ProductRecommendation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommend", tags=["recommendations"])

# Global cache service
cache_service = CacheService()


@router.get("/health", response_model=dict)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "cache_available": cache_service.is_available()
    }


@router.get("/products", response_model=RecommendationResponse)
async def get_product_recommendations(
        seller_id: str = Query(..., description="The seller requesting recommendations"),
        limit: int = Query(20, ge=1, le=100, description="Max products to return"),
        exclude_ids: Optional[str] = Query(None, description="Comma-separated product IDs to exclude"),
        db: Session = Depends(get_db)
):
    """
    Get product recommendations for a seller

    Query Parameters:
    - seller_id (required): Seller UUID
    - limit: Number of products (1-100, default 20)
    - exclude_ids: Comma-separated IDs like "1,2,3" to exclude from results

    Example:
        GET /api/recommend/products?seller_id=seller123&limit=20&exclude_ids=1,2,3

    Response:
        {
            "seller_id": "seller123",
            "recommendations": [
                {
                    "product_id": 42,
                    "name": "Premium Fabric",
                    "code": "FAB-001",
                    "score": 87.5,
                    "rank": 1,
                    "category_id": 5,
                    "selling_price": 99.99,
                    "rating_stars": 4,
                    "sources": {
                        "popularity": 85.0,
                        "history": 90.0,
                        "engagement": 70.0,
                        "recency": 95.0,
                        "newness": 60.0
                    }
                }
            ],
            "count": 20,
            "generated_at": "2024-01-15T10:30:00Z",
            "cache_hit": false
        }
    """
    try:
        start_time = time.time()

        # Validate seller_id
        if not seller_id or not seller_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="seller_id is required"
            )

        # Parse exclude_ids
        exclude_product_ids = []
        if exclude_ids:
            try:
                exclude_product_ids = [int(id.strip()) for id in exclude_ids.split(",")]
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="exclude_ids must be comma-separated integers"
                )

        # Check cache first
        cache_hit = False
        cached_recommendations = cache_service.get_recommendations(seller_id)

        if cached_recommendations:
            cache_hit = True
            recommendations = cached_recommendations
            logger.info(f"Served {len(recommendations)} recommendations from cache for {seller_id}")
        else:
            # Compute recommendations
            engine = RecommendationEngine(db)
            recommendations = await engine.compute_recommendations(
                seller_id=seller_id,
                limit=limit,
                exclude_product_ids=exclude_product_ids
            )

            # Add product details
            recommendations = engine.add_product_details(recommendations)

            # Cache the results
            cache_service.set_recommendations(seller_id, recommendations)

        # Apply exclude filter (in case it wasn't used during computation)
        if exclude_product_ids:
            recommendations = [
                r for r in recommendations
                if r['product_id'] not in exclude_product_ids
            ][:limit]

        elapsed = time.time() - start_time

        response = RecommendationResponse(
            seller_id=seller_id,
            recommendations=[
                ProductRecommendation(**rec) for rec in recommendations
            ],
            count=len(recommendations),
            cache_hit=cache_hit,
            elapsed_ms=round(elapsed * 1000, 2)
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing recommendations for {seller_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute recommendations"
        )


@router.post("/invalidate", response_model=dict)
async def invalidate_cache(
        seller_id: Optional[str] = Query(None),
        product_id: Optional[int] = Query(None),
        all: bool = Query(False)
):
    """
    Invalidate recommendation caches

    Call this when:
    - User places an order (invalidate their seller cache)
    - Product is created/modified (invalidate popular products)
    - System-wide updates (use all=true)

    Query Parameters:
    - seller_id: Invalidate cache for specific seller
    - product_id: Invalidate popular products cache (used when product is ordered)
    - all: Invalidate all caches (use sparingly)

    Example:
        POST /api/recommend/invalidate?seller_id=seller123
        POST /api/recommend/invalidate?product_id=42
        POST /api/recommend/invalidate?all=true
    """
    try:
        invalidated = False

        if all:
            cache_service.invalidate_all()
            invalidated = True
            logger.info("Invalidated all recommendation caches")
        elif seller_id:
            cache_service.invalidate_seller(seller_id)
            invalidated = True
            logger.info(f"Invalidated cache for seller {seller_id}")
        elif product_id:
            cache_service.invalidate_product(product_id)
            invalidated = True
            logger.info(f"Invalidated cache due to product {product_id}")

        return {
            "success": invalidated,
            "message": "Cache invalidated" if invalidated else "No cache invalidated"
        }

    except Exception as e:
        logger.error(f"Error invalidating cache: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to invalidate cache"
        )


@router.get("/debug/popular", response_model=dict)
async def debug_popular_products(
        limit: int = Query(50, ge=1, le=100),
        db: Session = Depends(get_db)
):
    """
    DEBUG ENDPOINT: Get popular products (unscoped)

    Shows trending products across all sellers.
    Used for debugging and monitoring popularity algorithm.
    """
    try:
        from ..services.data_service import DataService

        data_service = DataService(db)
        popular = data_service.get_popular_products(
            seller_id="",  # Not needed for this query
            limit=limit
        )

        return {
            "popular_products": popular,
            "count": len(popular)
        }

    except Exception as e:
        logger.error(f"Error fetching popular products: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch popular products"
        )


@router.get("/debug/seller/{seller_id}", response_model=dict)
async def debug_seller_data(
        seller_id: str,
        db: Session = Depends(get_db)
):
    """
    DEBUG ENDPOINT: Get seller's recommendation data

    Shows all signals used in recommendation computation for this seller.
    Useful for understanding why certain products are recommended.
    """
    try:
        from ..services.data_service import DataService

        data_service = DataService(db)

        # Get all data signals
        popular = data_service.get_popular_products(seller_id, limit=20)
        history = data_service.get_seller_order_history(seller_id)
        categories = data_service.get_seller_category_preferences(seller_id)
        prefs = data_service.get_seller_preferences_from_db(seller_id)

        return {
            "seller_id": seller_id,
            "popular_products": popular,
            "order_history_count": len(history),
            "category_preferences": categories,
            "stored_preferences": prefs
        }

    except Exception as e:
        logger.error(f"Error fetching seller debug data: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch seller data"
        )
