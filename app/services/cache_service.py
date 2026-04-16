"""
Redis cache service for recommendations
Handles caching, TTL, and invalidation
"""

import json
import logging
from typing import List, Dict, Any, Optional
import redis
from datetime import timedelta

from ..config import settings

logger = logging.getLogger(__name__)


class CacheService:
    """Redis wrapper for recommendation caching"""

    # Cache key prefixes
    PREFIX_RECOMMENDATIONS = "rec:products:"
    PREFIX_POPULAR = "rec:popular:"
    PREFIX_SELLER_PREFS = "rec:prefs:"
    PREFIX_ENGAGEMENT = "rec:engagement:"
    PREFIX_HEALTH = "rec:health"

    # Default TTL (1 hour)
    DEFAULT_TTL = 3600

    def __init__(self):
        """Initialize Redis connection"""
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
        """Check if Redis is available"""
        if not self.redis:
            return False
        try:
            self.redis.ping()
            return True
        except Exception as e:
            logger.warning(f"Redis health check failed: {str(e)}")
            return False

    def get_recommendations(
            self,
            seller_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached recommendations for seller

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
        Cache recommendations for seller

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

    def get_popular_products(
            self,
            cache_key: str = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached popular products global list

        Args:
            cache_key: Optional custom cache key

        Returns:
            Cached products or None
        """
        if not self.is_available():
            return None

        try:
            key = cache_key or f"{self.PREFIX_POPULAR}global"
            cached = self.redis.get(key)

            if cached:
                logger.info(f"Cache hit for popular products")
                return json.loads(cached)

            return None

        except Exception as e:
            logger.error(f"Error reading popular products cache: {str(e)}")
            return None

    def set_popular_products(
            self,
            products: List[Dict[str, Any]],
            cache_key: str = None,
            ttl: int = None
    ) -> bool:
        """
        Cache popular products list

        Args:
            products: List of popular products
            cache_key: Optional custom cache key
            ttl: Time-to-live in seconds

        Returns:
            True if cached successfully
        """
        if not self.is_available():
            return False

        try:
            key = cache_key or f"{self.PREFIX_POPULAR}global"
            ttl = ttl or (self.DEFAULT_TTL * 2)  # 2 hours for popular products

            self.redis.setex(
                key,
                ttl,
                json.dumps(products)
            )
            logger.info(f"Cached {len(products)} popular products, TTL={ttl}s")
            return True

        except Exception as e:
            logger.error(f"Error caching popular products: {str(e)}")
            return False

    def get_seller_preferences(
            self,
            seller_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached seller preferences

        Args:
            seller_id: The seller ID

        Returns:
            Cached preferences or None
        """
        if not self.is_available():
            return None

        try:
            key = f"{self.PREFIX_SELLER_PREFS}{seller_id}"
            cached = self.redis.get(key)

            if cached:
                logger.info(f"Cache hit for seller preferences {seller_id}")
                return json.loads(cached)

            return None

        except Exception as e:
            logger.error(f"Error reading seller preferences cache: {str(e)}")
            return None

    def set_seller_preferences(
            self,
            seller_id: str,
            preferences: Dict[str, Any],
            ttl: int = None
    ) -> bool:
        """
        Cache seller preferences

        Args:
            seller_id: The seller ID
            preferences: Preferences dict
            ttl: Time-to-live in seconds

        Returns:
            True if cached successfully
        """
        if not self.is_available():
            return False

        try:
            key = f"{self.PREFIX_SELLER_PREFS}{seller_id}"
            ttl = ttl or self.DEFAULT_TTL

            self.redis.setex(
                key,
                ttl,
                json.dumps(preferences)
            )
            logger.info(f"Cached seller preferences for {seller_id}, TTL={ttl}s")
            return True

        except Exception as e:
            logger.error(f"Error caching seller preferences: {str(e)}")
            return False

    def invalidate_seller(self, seller_id: str) -> bool:
        """
        Invalidate all cache for a seller (when they place new order, etc)

        Args:
            seller_id: The seller ID

        Returns:
            True if invalidated
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

    def invalidate_product(self, product_id: int) -> bool:
        """
        Invalidate popular products cache (when product is ordered)

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

    def invalidate_all(self) -> bool:
        """
        Invalidate all recommendation caches
        Use sparingly - only on major data changes

        Returns:
            True if invalidated
        """
        if not self.is_available():
            return False

        try:
            # Find all keys with recommendation prefixes
            pattern = "rec:*"
            keys = self.redis.keys(pattern)

            if keys:
                self.redis.delete(*keys)
                logger.warning(f"Invalidated all {len(keys)} recommendation caches")

            return True

        except Exception as e:
            logger.error(f"Error invalidating all caches: {str(e)}")
            return False

    def set_health_status(self, status: str) -> bool:
        """
        Store last health check status

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
        """Close Redis connection"""
        if self.redis:
            try:
                self.redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis: {str(e)}")
