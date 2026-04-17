"""
Redis cache service for recommendations.
Handles caching, TTL, and invalidation with graceful degradation.
"""

import json
import logging
from typing import List, Dict, Any, Optional

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CacheService:
    """Redis wrapper for recommendation caching."""

    # Cache key prefixes
    PREFIX_RECOMMENDATIONS = "rec:products:"
    PREFIX_POPULAR = "rec:popular:"
    PREFIX_SELLER_PREFS = "rec:prefs:"
    PREFIX_HEALTH = "rec:health"

    # Default TTL (1 hour)
    DEFAULT_TTL = 3600

    def __init__(self):
        """Initialize Redis connection."""
        try:
            self.redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
            # Test connection
            self.redis.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            self.redis = None

    def is_available(self) -> bool:
        """Check if Redis is available."""
        if not self.redis:
            return False
        try:
            self.redis.ping()
            return True
        except Exception as e:
            logger.warning(f"Redis health check failed: {str(e)}")
            return False

    # ==================== RECOMMENDATIONS ====================

    def get_recommendations(
            self,
            seller_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached recommendations for seller.

        Args:
            seller_id: The seller ID

        Returns:
            Cached recommendations or None if not found/expired
        """
        if not self.is_available():
            return None

        try:
            key = f"{self.PREFIX_RECOMMENDATIONS}{seller_id}"
            cached = self.redis.get(key)

            if cached:
                logger.info(f"Cache hit for seller {seller_id}")
                return json.loads(cached)

            logger.info(f"Cache miss for seller {seller_id}")
            return None

        except Exception as e:
            logger.error(f"Error reading recommendations cache: {str(e)}")
            return None

    def set_recommendations(
            self,
            seller_id: str,
            recommendations: List[Dict[str, Any]],
            ttl: int = None
    ) -> bool:
        """
        Cache recommendations for seller.

        Args:
            seller_id: The seller ID
            recommendations: List of recommendations to cache
            ttl: Time-to-live in seconds (default 1 hour)

        Returns:
            True if cached successfully
        """
        if not self.is_available():
            return False

        try:
            key = f"{self.PREFIX_RECOMMENDATIONS}{seller_id}"
            ttl = ttl or self.DEFAULT_TTL

            self.redis.setex(
                key,
                ttl,
                json.dumps(recommendations)
            )
            logger.info(f"Cached recommendations for seller {seller_id}, TTL={ttl}s")
            return True

        except Exception as e:
            logger.error(f"Error caching recommendations: {str(e)}")
            return False

    # ==================== INVALIDATION ====================

    def delete(self, seller_id: str) -> bool:
        """
        Delete cached recommendations for a specific seller.

        Args:
            seller_id: The seller ID

        Returns:
            True if deleted
        """
        if not self.is_available():
            return False

        try:
            keys_to_delete = [
                f"{self.PREFIX_RECOMMENDATIONS}{seller_id}",
                f"{self.PREFIX_SELLER_PREFS}{seller_id}"
            ]
            if self.redis.delete(*keys_to_delete) > 0:
                logger.info(f"Invalidated cache for seller {seller_id}")
            return True

        except Exception as e:
            logger.error(f"Error invalidating cache: {str(e)}")
            return False

    def invalidate_seller(self, seller_id: str) -> bool:
        """Alias for delete(). Invalidate all cache for a seller."""
        return self.delete(seller_id)

    def invalidate_product(self, product_id: int) -> bool:
        """
        Invalidate popular products cache (when product is ordered).

        Args:
            product_id: The product ID

        Returns:
            True if invalidated
        """
        if not self.is_available():
            return False

        try:
            key = f"{self.PREFIX_POPULAR}global"
            self.redis.delete(key)
            logger.info(f"Invalidated popular products cache due to product {product_id}")
            return True

        except Exception as e:
            logger.error(f"Error invalidating product cache: {str(e)}")
            return False

    def clear_all(self) -> bool:
        """
        Invalidate all recommendation caches.
        Use sparingly — only on major data changes.

        Uses SCAN instead of KEYS to avoid blocking Redis.

        Returns:
            True if invalidated
        """
        if not self.is_available():
            return False

        try:
            pattern = "rec:*"
            deleted_count = 0
            cursor = 0

            # Use SCAN instead of KEYS to avoid blocking Redis
            while True:
                cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    self.redis.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break

            if deleted_count > 0:
                logger.warning(f"Invalidated all {deleted_count} recommendation caches")

            return True

        except Exception as e:
            logger.error(f"Error invalidating all caches: {str(e)}")
            return False

    def invalidate_all(self) -> bool:
        """Alias for clear_all()."""
        return self.clear_all()

    # ==================== HEALTH ====================

    def set_health_status(self, status: str) -> bool:
        """
        Store last health check status.

        Args:
            status: "healthy" or "unhealthy"

        Returns:
            True if set
        """
        if not self.is_available():
            return False

        try:
            self.redis.setex(
                self.PREFIX_HEALTH,
                300,  # 5 minutes
                status
            )
            return True
        except Exception as e:
            logger.error(f"Error setting health status: {str(e)}")
            return False

    def close(self):
        """Close Redis connection."""
        if self.redis:
            try:
                self.redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis: {str(e)}")
