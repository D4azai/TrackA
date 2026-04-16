"""
Production Configuration for Recommendation Service

Security:
- Database credentials from environment variables
- CORS restricted to specific origins
- Algorithm weights configurable
- Environment-specific logging

No hardcoded credentials or secrets.
"""

import logging
from typing import Dict, List
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Application settings from environment variables.
    
    All critical values come from .env file, never hardcoded.
    """
    
    # ============= DATABASE =============
    DATABASE_URL: str = Field(..., description="PostgreSQL connection string")
    DATABASE_URL_UNPOOLED: str = Field(
        ..., 
        description="Unpooled connection for migrations"
    )
    
    # Connection pool settings
    DB_POOL_SIZE: int = Field(default=10, description="Connection pool size")
    DB_MAX_OVERFLOW: int = Field(default=20, description="Max pool overflow")
    DB_POOL_TIMEOUT: int = Field(default=30, description="Pool timeout in seconds")
    DB_POOL_RECYCLE: int = Field(default=3600, description="Recycle connections after 1 hour")
    
    # ============= REDIS =============
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string"
    )
    
    # ============= SECURITY =============
    ENVIRONMENT: str = Field(
        default="development",
        description="Environment: development, staging, production"
    )
    
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed CORS origins (NOT '*')"
    )
    
    ALLOWED_HOSTS: List[str] = Field(
        default=["localhost", "127.0.0.1"],
        description="Allowed Host header values"
    )
    
    API_KEY: str = Field(
        default="dev-key",
        description="API key for service-to-service auth (if needed)"
    )
    
    # ============= ALGORITHM =============
    
    # Signal weights: must sum to 1.0
    POPULARITY_WEIGHT: float = Field(default=0.25, ge=0, le=1)
    HISTORY_WEIGHT: float = Field(default=0.35, ge=0, le=1)
    ENGAGEMENT_WEIGHT: float = Field(default=0.05, ge=0, le=1)
    RECENCY_WEIGHT: float = Field(default=0.20, ge=0, le=1)
    NEWNESS_WEIGHT: float = Field(default=0.15, ge=0, le=1)
    
    # Recency decay parameters (tunable for different businesses)
    RECENCY_HALF_LIFE_DAYS: int = Field(
        default=30,
        description="Days until recency score is 50%"
    )
    
    # History lookback period
    HISTORY_LOOKBACK_DAYS: int = Field(
        default=90,
        description="Days of order history to consider"
    )
    
    # Recommendation parameters
    DEFAULT_LIMIT: int = Field(default=30, ge=1, le=100)
    MAX_LIMIT: int = Field(default=100, ge=1)
    MIN_SCORE_THRESHOLD: float = Field(
        default=0.0,
        description="Minimum score to include in results"
    )
    
    # ============= CACHING =============
    CACHE_ENABLED: bool = Field(default=True)
    CACHE_TTL_SECONDS: int = Field(
        default=3600,
        description="Cache time-to-live in seconds"
    )
    
    # ============= LOGGING =============
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # ============= FEATURE FLAGS =============
    ENABLE_BATCH_QUERIES: bool = Field(
        default=True,
        description="Use batch queries instead of N+1"
    )
    ENABLE_SELLER_SCOPED_SIGNALS: bool = Field(
        default=True,
        description="Use seller-specific signal scoping"
    )
    ENABLE_COLLABORATIVE_FILTERING: bool = Field(
        default=False,
        description="Enable collaborative filtering (requires precomputation)"
    )
    
    class Config:
        """Pydantic config"""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    @property
    def algorithm_weights(self) -> Dict[str, float]:
        """Get algorithm weights dictionary"""
        weights = {
            "popularity": self.POPULARITY_WEIGHT,
            "history": self.HISTORY_WEIGHT,
            "engagement": self.ENGAGEMENT_WEIGHT,
            "recency": self.RECENCY_WEIGHT,
            "newness": self.NEWNESS_WEIGHT,
        }
        
        # Validate weights sum to ~1.0 (allow small floating point errors)
        total = sum(weights.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"Algorithm weights must sum to 1.0, got {total}. "
                f"Check: POPULARITY_WEIGHT, HISTORY_WEIGHT, ENGAGEMENT_WEIGHT, "
                f"RECENCY_WEIGHT, NEWNESS_WEIGHT in environment"
            )
        
        return weights
    
    @property
    def environment(self) -> str:
        """Get environment name"""
        return self.ENVIRONMENT.lower()
    
    @property
    def is_production(self) -> bool:
        """Check if production environment"""
        return self.environment == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if development environment"""
        return self.environment == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get settings singleton.
    
    Cached to avoid re-reading .env file on every request.
    """
    return Settings()


# ============= LOGGING SETUP =============
def setup_logging():
    """Configure logging based on settings"""
    settings = get_settings()
    
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format=settings.LOG_FORMAT
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


if __name__ == "__main__":
    # Print current settings (for debugging, never in production!)
    settings = get_settings()
    print(f"Environment: {settings.environment}")
    print(f"Database: {settings.DATABASE_URL[:50]}...")
    print(f"Algorithm Weights: {settings.algorithm_weights}")
    print(f"Cache TTL: {settings.CACHE_TTL_SECONDS}s")
