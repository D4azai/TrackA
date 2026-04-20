import logging
from typing import Dict
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MLWeightOptimizer:
    """
    Contextual heuristic to dynamically adjust signal weights per seller.
    This acts as a placeholder for a true ML model (e.g., Multi-Armed Bandit or
    Deep Recommender) by adapting weights based on the seller's profile.
    """

    def __init__(self):
        self.base_weights = settings.algorithm_weights
        self.cold_start_threshold = settings.cold_start_threshold

    def get_weights_for_seller(self, seller_history_size: int) -> Dict[str, float]:
        """
        Adjust weights dynamically based on seller history.
        - Cold start users get higher weight on newness and popularity.
        - Power users get higher weight on their specific history and recency.
        """
        if not settings.enable_ml_weights:
            return self.base_weights.copy()

        weights = self.base_weights.copy()

        if seller_history_size < self.cold_start_threshold:
            # Cold Start Profile
            # Shift weight from history to newness and popularity
            history_shift = weights["history"] * 0.6  # take away 60% of history weight
            weights["history"] -= history_shift
            
            # Distribute shifted weight to popularity and newness
            weights["newness"] += history_shift * 0.6
            weights["popularity"] += history_shift * 0.4
            
            logger.debug(f"Applied cold-start ML weights (history size {seller_history_size})")

        elif seller_history_size > 50:
            # Power User Profile
            # Shift weight from popularity to history and recency
            pop_shift = weights["popularity"] * 0.5  # take away 50% of popularity weight
            weights["popularity"] -= pop_shift
            
            # Distribute to history and recency
            weights["history"] += pop_shift * 0.7
            weights["recency"] += pop_shift * 0.3
            
            logger.debug(f"Applied power-user ML weights (history size {seller_history_size})")

        # Normalize to ensure sum is exactly 1.0 (to avoid floating point drift)
        total = sum(weights.values())
        if total > 0:
            for k in weights:
                weights[k] = round(weights[k] / total, 3)

        return weights
