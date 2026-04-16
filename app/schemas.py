"""
Pydantic models for request/response validation
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime


class ProductRecommendation(BaseModel):
    """A single recommended product"""
    product_id: int = Field(..., description="Product ID")
    name: Optional[str] = Field(None, description="Product name")
    code: Optional[str] = Field(None, description="Product code")
    score: float = Field(..., description="Recommendation score (0-100)")
    rank: int = Field(..., description="Rank in recommendation list (1-based)")
    category_id: Optional[int] = None
    selling_price: Optional[float] = None
    rating_stars: Optional[int] = None
    sources: Dict[str, float] = Field(
        default_factory=dict,
        description="Scores by signal: popularity, history, engagement, recency, newness"
    )


class RecommendationRequest(BaseModel):
    """Request for product recommendations"""
    seller_id: str = Field(..., description="Seller UUID")
    limit: int = Field(20, ge=1, le=100, description="Max products to return")
    exclude_ids: Optional[List[int]] = Field(None, description="Product IDs to exclude")


class RecommendationResponse(BaseModel):
    """Response with product recommendations"""
    seller_id: str = Field(..., description="The requesting seller")
    recommendations: List[ProductRecommendation] = Field(..., description="List of recommended products")
    count: int = Field(..., description="Number of recommendations")
    cache_hit: bool = Field(default=False, description="Whether result came from cache")
    elapsed_ms: Optional[float] = Field(None, description="Time to compute (milliseconds)")
    generated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str = Field(..., description="Service status: healthy/unhealthy")
    cache_available: bool = Field(..., description="Whether cache is available")


class CacheInvalidationRequest(BaseModel):
    """Request to invalidate cache"""
    seller_id: Optional[str] = Field(None, description="Seller ID to invalidate")
    product_id: Optional[int] = Field(None, description="Product ID to invalidate")
    all: bool = Field(False, description="Invalidate all caches")
