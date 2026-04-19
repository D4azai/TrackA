"""
Recommendation API endpoints.

Serving path:
- Redis hot cache
- PostgreSQL durable precomputed snapshots
- Background refresh queue
- Optional synchronous fallback in development
"""

import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.meta import VERSION
from app.services.cache_service import CacheService
from app.services.precomputed_service import (
    PrecomputedRecommendationService,
)
from app.services.refresh_service import RecommendationRefreshService

try:
    from app.metrics import (
        RECOMMENDATION_CACHE_TOTAL,
        RECOMMENDATION_PRECOMPUTED_TOTAL,
        RECOMMENDATION_RESPONSE_SOURCE_TOTAL,
    )

    _metrics_available = True
except ImportError:
    _metrics_available = False

logger = logging.getLogger(__name__)
settings = get_settings()


class RecommendationSource(BaseModel):
    """Score breakdown per signal."""

    popularity: float = Field(..., ge=0, description="Popularity signal contribution")
    history: float = Field(..., ge=0, description="History signal contribution")
    recency: float = Field(..., ge=0, description="Recency signal contribution")
    newness: float = Field(..., ge=0, description="Newness signal contribution")
    engagement: float = Field(..., ge=0, description="Engagement signal contribution")


class Recommendation(BaseModel):
    """Single product recommendation."""

    product_id: int
    score: float = Field(..., ge=0, le=100, description="Final recommendation score (0-100)")
    rank: int = Field(..., ge=1, description="Ranking position (1 = best)")
    is_personalized: bool = Field(
        default=False,
        description="True if seller-specific signals influenced this recommendation",
    )
    sources: RecommendationSource = Field(..., description="Score breakdown by signal")


class RecommendationResponse(BaseModel):
    """Stable response schema for the Next.js consumer."""

    seller_id: str
    recommendations: List[Recommendation]
    count: int = Field(..., ge=0)
    cache_hit: bool = Field(default=False)
    personalized: int = Field(default=0, description="Number of personalized results")
    elapsed_ms: Optional[float] = Field(None, description="Request handling time in ms")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    cache: str = Field(default="unknown")


class SellerRefreshRequest(BaseModel):
    seller_id: str = Field(..., min_length=1, max_length=128)
    requested_by: Optional[str] = Field(default=None, max_length=128)


class OrderPlacedEvent(BaseModel):
    seller_id: str = Field(..., min_length=1, max_length=128)
    order_id: Optional[int] = None
    requested_by: Optional[str] = Field(default=None, max_length=128)


class ProductEngagementEvent(BaseModel):
    seller_id: str = Field(..., min_length=1, max_length=128)
    product_id: Optional[int] = None
    event_type: str = Field(default="liked", max_length=64)
    requested_by: Optional[str] = Field(default=None, max_length=128)


class ProductUpdatedEvent(BaseModel):
    product_id: Optional[int] = None
    requested_by: Optional[str] = Field(default=None, max_length=128)
    seller_limit: Optional[int] = Field(default=None, ge=1)


class ActiveSellerRefreshRequest(BaseModel):
    requested_by: Optional[str] = Field(default=None, max_length=128)
    seller_limit: Optional[int] = Field(default=None, ge=1)


class RefreshJobResponse(BaseModel):
    status: str
    seller_id: str
    job_id: Optional[int] = None
    created: bool = False


class BulkRefreshResponse(BaseModel):
    status: str
    trigger: str
    queued: int
    already_queued: int


class JobRunResponse(BaseModel):
    status: str
    processed: int
    succeeded: int
    failed: int


router = APIRouter()
cache_service = CacheService()


def build_precomputed_service(db: Session) -> PrecomputedRecommendationService:
    """Factory for durable recommendation snapshots."""
    return PrecomputedRecommendationService(db)


def build_refresh_service(db: Session) -> RecommendationRefreshService:
    """Factory for the background refresh orchestrator."""
    return RecommendationRefreshService(
        db_session=db,
        cache_service=cache_service,
    )


def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> None:
    """Validate admin API key for protected endpoints."""
    configured_key = settings.admin_api_key
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key not configured. Set ADMIN_API_KEY in environment.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )


def normalize_seller_id(seller_id: str) -> str:
    """Normalize seller IDs so cache keys, DB lookups, and logs stay consistent."""
    normalized = seller_id.strip()
    if not normalized:
        raise ValueError("seller_id must not be blank.")
    return normalized


def parse_cached_recommendations(cached: List[Dict[str, Any]], limit: int) -> List[Recommendation]:
    """Validate cached payloads and degrade to a cache miss if the shape is stale."""
    try:
        return [Recommendation(**item) for item in cached[:limit]]
    except (TypeError, ValidationError) as exc:
        raise ValueError("Cached recommendation payload is invalid.") from exc


def build_recommendation_response(
    seller_id: str,
    recommendations: List[Any],
    cache_hit: bool,
    elapsed_ms: float,
) -> RecommendationResponse:
    """Build the stable API payload from dicts or Recommendation objects."""
    normalized_recommendations = [
        item if isinstance(item, Recommendation) else Recommendation(**item)
        for item in recommendations
    ]
    return RecommendationResponse(
        seller_id=seller_id,
        recommendations=normalized_recommendations,
        count=len(normalized_recommendations),
        cache_hit=cache_hit,
        personalized=sum(1 for item in normalized_recommendations if item.is_personalized),
        elapsed_ms=round(elapsed_ms, 2),
    )


def record_response_source(source: str) -> None:
    if _metrics_available:
        RECOMMENDATION_RESPONSE_SOURCE_TOTAL.labels(source=source).inc()


def record_precomputed_result(result: str) -> None:
    if _metrics_available:
        RECOMMENDATION_PRECOMPUTED_TOTAL.labels(result=result).inc()


def warm_redis_cache(seller_id: str, recommendations: List[Dict[str, Any]]) -> None:
    if not settings.cache_enabled:
        return
    cache_service.set_recommendations(
        seller_id=seller_id,
        recommendations=recommendations,
        ttl=settings.recommendation_cache_ttl_seconds,
    )


def enqueue_refresh(
    refresh_service: RecommendationRefreshService,
    seller_id: str,
    trigger: str,
    limit: int,
) -> None:
    refresh_service.enqueue_seller_refresh(
        seller_id=seller_id,
        trigger=trigger,
        requested_by="request_path",
        details={"limit": limit},
    )


@router.get(
    "/products",
    response_model=RecommendationResponse,
    summary="Get seller-based product recommendations",
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
        le=settings.max_limit,
        description=f"Number of recommendations to return (1-{settings.max_limit})",
    ),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """
    Serve recommendations from Redis first, then durable PostgreSQL snapshots.

    Expensive recomputation moves to the refresh queue and worker path. Development
    keeps an inline fallback to make local iteration easier.
    """
    try:
        t_start = time.time()
        seller_id = normalize_seller_id(seller_id)
        precomputed_service = build_precomputed_service(db)
        refresh_service = build_refresh_service(db)

        if settings.cache_enabled:
            cached = cache_service.get_recommendations(seller_id)
            if cached is not None:
                try:
                    recommendations = parse_cached_recommendations(cached, limit)
                    if _metrics_available:
                        RECOMMENDATION_CACHE_TOTAL.labels(result="hit").inc()
                    record_response_source("redis")
                    return build_recommendation_response(
                        seller_id=seller_id,
                        recommendations=recommendations,
                        cache_hit=True,
                        elapsed_ms=(time.time() - t_start) * 1000,
                    )
                except ValueError:
                    logger.warning(
                        "Ignoring invalid cached recommendations for seller %r; recomputing.",
                        seller_id,
                    )
                    cache_service.delete(seller_id)
            if _metrics_available:
                RECOMMENDATION_CACHE_TOTAL.labels(result="miss").inc()

        snapshot = precomputed_service.get_latest_snapshot(seller_id, limit)
        if snapshot is not None:
            if snapshot.is_fresh:
                record_precomputed_result("fresh")
                record_response_source("precomputed_fresh")
                warm_redis_cache(seller_id, snapshot.recommendations)
                return build_recommendation_response(
                    seller_id=seller_id,
                    recommendations=snapshot.recommendations,
                    cache_hit=False,
                    elapsed_ms=(time.time() - t_start) * 1000,
                )

            record_precomputed_result("stale")
            if settings.serve_stale_precomputed:
                enqueue_refresh(refresh_service, seller_id, "snapshot_stale", limit)
                record_response_source("precomputed_stale")
                return build_recommendation_response(
                    seller_id=seller_id,
                    recommendations=snapshot.recommendations,
                    cache_hit=False,
                    elapsed_ms=(time.time() - t_start) * 1000,
                )
        else:
            record_precomputed_result("missing")

        if settings.allow_sync_recompute_fallback:
            refreshed = refresh_service.refresh_seller_now(
                seller_id=seller_id,
                trigger="sync_fallback",
            )
            record_response_source("sync_fallback")
            return build_recommendation_response(
                seller_id=seller_id,
                recommendations=refreshed[:limit],
                cache_hit=False,
                elapsed_ms=(time.time() - t_start) * 1000,
            )

        enqueue_refresh(refresh_service, seller_id, "request_miss", limit)
        record_response_source("queued_empty")
        return build_recommendation_response(
            seller_id=seller_id,
            recommendations=[],
            cache_hit=False,
            elapsed_ms=(time.time() - t_start) * 1000,
        )

    except ValueError as exc:
        logger.warning("Validation error for seller %r: %s", seller_id, exc)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(
            "Error serving recommendations for seller %r: %s",
            seller_id,
            exc,
            exc_info=True,
        )
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
    """Lightweight health check for load balancers and uptime monitors."""
    cache_status = "ok" if cache_service.is_healthy() else "unavailable"
    return HealthResponse(
        status="healthy",
        service="recommendation-engine",
        version=VERSION,
        cache=cache_status,
    )


@router.post(
    "/refresh/seller",
    response_model=RefreshJobResponse,
    summary="Queue a refresh job for one seller",
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
)
async def queue_seller_refresh(
    payload: SellerRefreshRequest,
    db: Session = Depends(get_db),
) -> RefreshJobResponse:
    seller_id = normalize_seller_id(payload.seller_id)
    refresh_service = build_refresh_service(db)
    result = refresh_service.enqueue_seller_refresh(
        seller_id=seller_id,
        trigger="manual_admin",
        requested_by=payload.requested_by or "admin",
    )
    return RefreshJobResponse(
        status="queued" if result.created else "already_queued",
        seller_id=seller_id,
        job_id=result.job_id,
        created=result.created,
    )


@router.post(
    "/events/order-placed",
    response_model=RefreshJobResponse,
    summary="Queue a seller refresh after an order is placed",
    tags=["Events"],
    dependencies=[Depends(require_api_key)],
)
async def order_placed_event(
    payload: OrderPlacedEvent,
    db: Session = Depends(get_db),
) -> RefreshJobResponse:
    seller_id = normalize_seller_id(payload.seller_id)
    refresh_service = build_refresh_service(db)
    result = refresh_service.enqueue_seller_refresh(
        seller_id=seller_id,
        trigger="order_placed",
        requested_by=payload.requested_by or "event",
        details={"order_id": payload.order_id},
    )
    return RefreshJobResponse(
        status="queued" if result.created else "already_queued",
        seller_id=seller_id,
        job_id=result.job_id,
        created=result.created,
    )


@router.post(
    "/events/product-engaged",
    response_model=RefreshJobResponse,
    summary="Queue a seller refresh after a seller-level product engagement signal",
    tags=["Events"],
    dependencies=[Depends(require_api_key)],
)
async def product_engaged_event(
    payload: ProductEngagementEvent,
    db: Session = Depends(get_db),
) -> RefreshJobResponse:
    seller_id = normalize_seller_id(payload.seller_id)
    refresh_service = build_refresh_service(db)
    result = refresh_service.enqueue_seller_refresh(
        seller_id=seller_id,
        trigger="product_engaged",
        requested_by=payload.requested_by or "event",
        details={
            "product_id": payload.product_id,
            "event_type": payload.event_type,
        },
    )
    return RefreshJobResponse(
        status="queued" if result.created else "already_queued",
        seller_id=seller_id,
        job_id=result.job_id,
        created=result.created,
    )


@router.post(
    "/events/product-updated",
    response_model=BulkRefreshResponse,
    summary="Queue refreshes for active sellers after a product create/update event",
    tags=["Events"],
    dependencies=[Depends(require_api_key)],
)
async def product_updated_event(
    payload: ProductUpdatedEvent,
    db: Session = Depends(get_db),
) -> BulkRefreshResponse:
    refresh_service = build_refresh_service(db)
    result = refresh_service.enqueue_active_sellers(
        trigger="product_updated",
        requested_by=payload.requested_by or "event",
        limit=payload.seller_limit,
        details={"product_id": payload.product_id},
    )
    return BulkRefreshResponse(
        status="queued",
        trigger="product_updated",
        queued=result["queued"],
        already_queued=result["already_queued"],
    )


@router.post(
    "/refresh/active",
    response_model=BulkRefreshResponse,
    summary="Queue scheduled refreshes for active sellers",
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
)
async def queue_active_seller_refresh(
    payload: ActiveSellerRefreshRequest,
    db: Session = Depends(get_db),
) -> BulkRefreshResponse:
    refresh_service = build_refresh_service(db)
    result = refresh_service.enqueue_active_sellers(
        trigger="scheduled_refresh",
        requested_by=payload.requested_by or "scheduler",
        limit=payload.seller_limit,
    )
    return BulkRefreshResponse(
        status="queued",
        trigger="scheduled_refresh",
        queued=result["queued"],
        already_queued=result["already_queued"],
    )


@router.post(
    "/jobs/run",
    response_model=JobRunResponse,
    summary="Process queued recommendation refresh jobs",
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
)
async def run_refresh_jobs(
    limit: int = Query(10, ge=1, le=500, description="Maximum queued jobs to process"),
    db: Session = Depends(get_db),
) -> JobRunResponse:
    refresh_service = build_refresh_service(db)
    summary = refresh_service.run_pending_jobs(limit=limit)
    return JobRunResponse(
        status="completed",
        processed=summary.processed,
        succeeded=summary.succeeded,
        failed=summary.failed,
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
) -> Dict[str, Any]:
    """Clear cached recommendations without touching durable Postgres snapshots."""
    try:
        if seller_id:
            normalized = normalize_seller_id(seller_id)
            cache_service.delete(normalized)
            logger.info("Admin: cleared cache for seller %r", normalized)
            return {"status": "cleared", "seller_id": normalized}

        cache_service.clear_all()
        logger.warning("Admin: cleared ALL recommendation caches")
        return {"status": "cleared", "scope": "all"}
    except Exception as exc:
        logger.error("Error clearing cache: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to clear cache")
