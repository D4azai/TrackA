"""
Configuration for Recommendation Service
Loads settings from environment variables
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

    # Database
    database_url: str
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis & Cache
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 3600  # 1 hour
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600  # 1 hour

    # Service
    service_name: str = "recommendation-service"
    service_port: int = 8001
    environment: str = "development"
    debug: bool = False

    # Algorithm
    algorithm_type: str = "hybrid"  # hybrid, collaborative, content_based
    batch_size: int = 1000
    precompute_interval: int = 3600
    max_limit: int = 100  # Maximum recommendations to return
    min_score_threshold: float = 0.0  # Minimum score to include in recommendations

    # Logging
    log_level: str = "INFO"

    # Recommendation weights
    weight_popularity: float = 0.25  # Real-time popularity
    weight_history: float = 0.35  # Seller's past orders
    weight_engagement: float = 0.05  # Likes/comments
    weight_recency: float = 0.2  # Recent orders
    weight_newness: float = 0.15  # New products

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()


def get_settings() -> Settings:
    """Get settings instance"""
    return settings
