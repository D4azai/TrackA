"""
Configuration for Recommendation Service

Loads settings from environment variables (.env file).
Uses pydantic-settings for validation and type coercion.
No hardcoded credentials or secrets.
"""

import logging
from typing import Dict, List, Optional
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

from app.meta import ALGORITHM_VERSION

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Application settings from environment variables.
    All critical values come from .env file, never hardcoded.
    """

    # ============= DATABASE =============
    database_url: str = Field(..., description="PostgreSQL connection string")
    database_pool_size: int = Field(default=10, description="Connection pool size")
    database_max_overflow: int = Field(default=20, description="Max pool overflow")
    database_pool_timeout: int = Field(default=30, description="Pool timeout in seconds")
    database_pool_recycle: int = Field(default=3600, description="Recycle connections after N seconds")

    # ============= REDIS & CACHE =============
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection string")
    cache_enabled: bool = Field(default=True)
    cache_ttl_seconds: int = Field(default=3600, description="Cache time-to-live in seconds")

    # ============= SERVICE =============
    service_name: str = "recommendation-service"
    service_port: int = 8000
    environment: str = Field(default="development", description="development, staging, or production")
    debug: bool = False

    # ============= SECURITY =============
    cors_origins: List[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed CORS origins"
    )
    allowed_hosts: List[str] = Field(
        default=["localhost", "127.0.0.1"],
        description="Allowed Host header values"
    )
    admin_api_key: Optional[str] = Field(
        default=None,
        description="API key required for admin endpoints (X-API-Key header)"
    )

    # ============= ALGORITHM =============
    weight_popularity: float = Field(default=0.25, ge=0, le=1, description="Popularity signal weight")
    weight_history: float = Field(default=0.35, ge=0, le=1, description="History signal weight")
    weight_engagement: float = Field(default=0.05, ge=0, le=1, description="Engagement signal weight")
    weight_recency: float = Field(default=0.20, ge=0, le=1, description="Recency signal weight")
    weight_newness: float = Field(default=0.15, ge=0, le=1, description="Newness signal weight")

    max_limit: int = Field(default=100, ge=1, description="Maximum recommendations per request")
    min_score_threshold: float = Field(default=0.0, description="Minimum score to include in results")
    recommendation_algorithm_version: str = Field(
        default=ALGORITHM_VERSION,
        description="Version tag stored with precomputed recommendation snapshots",
    )

    # ============= PRECOMPUTED STORAGE & REFRESH =============
    precomputed_store_limit: int = Field(
        default=100,
        ge=1,
        description="How many recommendations to store durably per seller snapshot",
    )
    precomputed_freshness_seconds: int = Field(
        default=1800,
        ge=1,
        description="Maximum age before a precomputed recommendation snapshot is considered stale",
    )
    serve_stale_precomputed: bool = Field(
        default=True,
        description="Serve stale Postgres snapshots while a background refresh is queued",
    )
    sync_recompute_fallback_enabled: Optional[bool] = Field(
        default=None,
        description="Force synchronous recomputation fallback. If unset, defaults to development only.",
    )
    refresh_active_sellers_lookback_days: int = Field(
        default=30,
        ge=1,
        description="Lookback window used to define active sellers for scheduled refreshes",
    )
    refresh_active_sellers_limit: int = Field(
        default=500,
        ge=1,
        description="Maximum active sellers enqueued by scheduled or catalog-wide refresh triggers",
    )
    refresh_event_priority: int = Field(
        default=150,
        ge=1,
        description="Priority assigned to event-driven refresh jobs",
    )
    refresh_schedule_priority: int = Field(
        default=100,
        ge=1,
        description="Priority assigned to scheduled refresh jobs",
    )
    refresh_manual_priority: int = Field(
        default=200,
        ge=1,
        description="Priority assigned to manual admin refresh jobs",
    )
    refresh_max_attempts: int = Field(
        default=3,
        ge=1,
        description="Max times a failed refresh job is retried before being permanently marked FAILED",
    )

    # ============= WORKER =============
    poll_interval_seconds: int = Field(
        default=30,
        ge=1,
        description="Seconds the standalone worker sleeps between job-queue polls",
    )
    worker_batch_size: int = Field(
        default=10,
        ge=1,
        description="Max jobs processed per poll cycle by the standalone worker",
    )

    # ============= LOGGING =============
    log_level: str = Field(default="INFO")

    # ============= FEATURE FLAGS =============
    enable_batch_queries: bool = Field(default=True, description="Use batch queries instead of N+1")
    enable_seller_scoped_signals: bool = Field(default=True, description="Use seller-specific signal scoping")
    enable_ml_weights: bool = Field(default=True, description="Use adaptive ML weights per seller")

    # ============= KAFKA =============
    kafka_bootstrap_servers: str = Field(default="kafka:29092", description="Kafka broker address")
    kafka_refresh_topic: str = Field(default="recommendation_refresh", description="Kafka topic for refresh events")

    # ============= ALGORITHM TUNING =============
    diversity_penalty_factor: float = Field(default=0.5, description="Penalty applied to duplicate categories")
    max_per_category: int = Field(default=2, description="Max products per category before penalty")
    cold_start_threshold: int = Field(default=5, description="Order count below which seller is considered cold start")
    random_exploration_ratio: float = Field(default=0.05, description="Percentage of recommendations that are random")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def algorithm_weights(self) -> Dict[str, float]:
        """Get algorithm weights dictionary with validation."""
        weights = {
            "popularity": self.weight_popularity,
            "history": self.weight_history,
            "engagement": self.weight_engagement,
            "recency": self.weight_recency,
            "newness": self.weight_newness,
        }
        total = sum(weights.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"Algorithm weights must sum to 1.0, got {total}. "
                f"Check: weight_popularity, weight_history, weight_engagement, "
                f"weight_recency, weight_newness in environment"
            )
        return weights

    @property
    def is_production(self) -> bool:
        """Check if production environment."""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if development environment."""
        return self.environment.lower() == "development"

    @property
    def allow_sync_recompute_fallback(self) -> bool:
        """Development keeps an inline fallback; production should rely on the worker path."""
        if self.sync_recompute_fallback_enabled is not None:
            return self.sync_recompute_fallback_enabled
        return self.is_development

    @property
    def recommendation_cache_ttl_seconds(self) -> int:
        """
        Hot-cache TTL should never outlive the freshness policy for durable snapshots.
        """
        return min(self.cache_ttl_seconds, self.precomputed_freshness_seconds)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get settings singleton.
    Cached to avoid re-reading .env file on every request.
    """
    return Settings()


# Module-level convenience (lazy — only instantiated on first access via get_settings)
settings = get_settings()


def setup_logging():
    """Configure logging based on settings."""
    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    # Reduce noise from third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
