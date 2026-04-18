"""
Redis cache service for recommendations.
Handles caching, TTL, and invalidation with graceful degradation.

Design decisions:
- No pre-flight PING on every call — overhead removed.
  Instead, each operation handles its own ConnectionError.
- Popular products cached separately (shared across all sellers).
- Graceful degradation: if Redis is unavailable the service
  continues working — just without caching.
"""

import json
import logging
from typing import List, Dict, Any, Optional

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CacheService:
    """Redis wrapper for recommendation caching with graceful degradation."""

    PREFIX_RECOMMENDATIONS = "rec:products:"
    PREFIX_POPULAR         = "rec:popular:global"
    PREFIX_SELLER_PREFS    = "rec:prefs:"

    DEFAULT_TTL         = 3600   # 1 hour
    POPULAR_TTL         = 900    # 15 minutes (popular changes faster)

    def __init__(self):
        self._client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Redis. Failures are non-fatal."""
        try:
            self._client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                socket_keepalive=True,
                retry_on_timeout=True,
            )
            self._client.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.warning(f"Redis unavailable — caching disabled: {e}")
            self._client = None

    def _get_client(self) -> Optional[redis.Redis]:
        """
        Return the Redis client, attempting reconnect if previously failed.
        Returns None if Redis is unavailable.
        """
        if self._client is not None:
            return self._client
        # Try reconnect (e.g. Redis restarted after service startup)
        self._connect()
        return self._client

    # ==================== RECOMMENDATIONS ====================

    def get_recommendations(self, seller_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get cached recommendations for a seller. Returns None on miss or error."""
        client = self._get_client()
        if not client:
            return None
        try:
            key = f"{self.PREFIX_RECOMMENDATIONS}{seller_id}"
            data = client.get(key)
            if data:
                logger.debug(f"Cache HIT for seller {seller_id}")
                return json.loads(data)
            logger.debug(f"Cache MISS for seller {seller_id}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis read error (recommendations): {e}")
            self._client = None   # force reconnect on next call
            return None

    def set_recommendations(
        self,
        seller_id: str,
        recommendations: List[Dict[str, Any]],
        ttl: int = None,
    ) -> bool:
        """Cache recommendations for a seller. Returns True on success."""
        client = self._get_client()
        if not client:
            return False
        try:
            key = f"{self.PREFIX_RECOMMENDATIONS}{seller_id}"
            client.setex(key, ttl or self.DEFAULT_TTL, json.dumps(recommendations))
            logger.debug(f"Cached {len(recommendations)} recs for seller {seller_id}, TTL={ttl or self.DEFAULT_TTL}s")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis write error (recommendations): {e}")
            self._client = None
            return False

    # ==================== POPULAR PRODUCTS ====================

    def get_popular(self) -> Optional[List[Dict[str, Any]]]:
        """Get globally cached popular-products list. Returns None on miss."""
        client = self._get_client()
        if not client:
            return None
        try:
            data = client.get(self.PREFIX_POPULAR)
            if data:
                logger.debug("Cache HIT for popular products")
                return json.loads(data)
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis read error (popular): {e}")
            self._client = None
            return None

    def set_popular(self, popular: List[Dict[str, Any]]) -> bool:
        """Cache the global popular-products list (15-min TTL)."""
        client = self._get_client()
        if not client:
            return False
        try:
            client.setex(self.PREFIX_POPULAR, self.POPULAR_TTL, json.dumps(popular))
            logger.debug(f"Cached {len(popular)} popular products, TTL={self.POPULAR_TTL}s")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis write error (popular): {e}")
            self._client = None
            return False

    # ==================== INVALIDATION ====================

    def delete(self, seller_id: str) -> bool:
        """Delete cached recommendations for a specific seller."""
        client = self._get_client()
        if not client:
            return False
        try:
            keys = [
                f"{self.PREFIX_RECOMMENDATIONS}{seller_id}",
                f"{self.PREFIX_SELLER_PREFS}{seller_id}",
            ]
            deleted = client.delete(*keys)
            if deleted:
                logger.info(f"Invalidated cache for seller {seller_id} ({deleted} keys)")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis delete error: {e}")
            self._client = None
            return False

    # Alias
    def invalidate_seller(self, seller_id: str) -> bool:
        return self.delete(seller_id)

    def clear_all(self) -> bool:
        """
        Invalidate ALL recommendation caches.
        Uses SCAN (cursor-based) to avoid blocking Redis in production.
        """
        client = self._get_client()
        if not client:
            return False
        try:
            pattern = "rec:*"
            deleted_count = 0
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    client.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break
            logger.warning(f"Cleared all {deleted_count} recommendation cache keys")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis clear_all error: {e}")
            self._client = None
            return False

    # Alias
    def invalidate_all(self) -> bool:
        return self.clear_all()

    # ==================== HEALTH ====================

    def is_healthy(self) -> bool:
        """Lightweight health check — PING Redis."""
        client = self._get_client()
        if not client:
            return False
        try:
            return client.ping()
        except Exception:
            return False

    def close(self) -> None:
        """Close Redis connection gracefully."""
        if self._client:
            try:
                self._client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.warning(f"Error closing Redis: {e}")
