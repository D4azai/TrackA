"""
Recommendation Algorithm — Production Ready

Weighted ensemble of 5 signals:
1. Popularity  (25%): Global trending products
2. History     (35%): Seller's past orders — with category affinity fallback
3. Recency     (20%): When seller last ordered (seller-scoped)
4. Newness     (15%): Product age
5. Engagement  ( 5%): Likes and comments

Category Affinity (embedded in History signal):
  For products not in the seller's direct order history, the history score
  is replaced with a discounted category-affinity score. This means sellers
  who frequently buy from a category will see new products from that category
  ranked higher — solving the cold-start product problem.

Performance: ~7 database queries total (5 signals + 1 affinity + 1 fallback if needed)
Response time target: <300ms (first load), <5ms (cache hit)
"""

import logging
import time
from typing import Dict, List

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.data_service import DataService

try:
    from app.metrics import RECOMMENDATION_COMPUTE_DURATION_SECONDS
    _metrics_available = True
except ImportError:
    _metrics_available = False

logger = logging.getLogger(__name__)
settings = get_settings()

# Discount applied to category affinity when the seller has never ordered
# the exact product — rewards category familiarity but less than direct history.
CATEGORY_AFFINITY_DISCOUNT = 0.45


class RecommendationEngine:
    """
    Production recommendation engine.

    Signals & weights (configurable via environment):
    - Popularity  (25%): Global trending products
    - History     (35%): Seller's past orders + category affinity fallback
    - Recency     (20%): When seller last ordered (seller-scoped)
    - Newness     (15%): Product age
    - Engagement  ( 5%): Likes and comments
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.data_service = DataService(db_session)
        self.settings = settings

    def compute_recommendations(
        self,
        seller_id: str,
        limit: int = 30,
    ) -> List[Dict]:
        """
        Compute personalised recommendations for a seller.

        Args:
            seller_id: Seller making request
            limit:     Number of recommendations (max configured by MAX_LIMIT)

        Returns:
            List of dicts with product_id, score, rank, sources, is_personalized

        Raises:
            RuntimeError: If a critical data-fetching error occurs
        """
        t_start = time.perf_counter()
        logger.info(f"Computing recommendations for seller {seller_id!r}, limit={limit}")

        limit = max(1, min(limit, self.settings.max_limit))

        # ======================================================
        # STEP 1: GATHER CANDIDATES
        # Primary sources: global popularity + seller history
        # Fallback: full catalog (sorted by rating) when pool is thin
        # ======================================================
        popular = self.data_service.get_popular_products(limit=limit * 2)
        history = self.data_service.get_seller_order_history(seller_id, limit=limit * 2)

        popularity_map: Dict[int, float] = {
            item["product_id"]: item["score"] for item in popular
        }

        candidate_ids = self._build_candidate_ids(popular, history, limit)

        # Catalog fallback — pad pool when popularity + history are thin
        needed = (limit * 2) - len(candidate_ids)
        if needed > 0:
            fallback_ids = self.data_service.get_catalog_fallback_products(
                limit=needed,
                exclude_ids=candidate_ids,
            )
            if fallback_ids:
                candidate_ids.extend(fallback_ids)
                logger.info(
                    f"Catalog fallback added {len(fallback_ids)} products "
                    f"(pool was thin: {len(candidate_ids) - len(fallback_ids)} from popularity/history)"
                )

        candidate_ids = candidate_ids[: limit * 2]

        if not candidate_ids:
            logger.warning(f"No candidate products found for seller {seller_id!r}")
            return []

        logger.info(f"Candidate pool: {len(candidate_ids)} products")

        # ======================================================
        # STEP 2: BATCH-FETCH ALL SIGNALS
        # Each call is a single DB query — no N+1 patterns
        # ======================================================
        engagement_data  = self.data_service.get_engagement_scores_batch(candidate_ids)
        recency_data     = self.data_service.get_recency_scores_batch(seller_id, candidate_ids)
        newness_data     = self.data_service.get_newness_scores_batch(candidate_ids)
        affinity_data    = self.data_service.get_category_affinity_scores(seller_id, candidate_ids)

        weights = {
            "popularity": self.settings.weight_popularity,
            "history":    self.settings.weight_history,
            "recency":    self.settings.weight_recency,
            "newness":    self.settings.weight_newness,
            "engagement": self.settings.weight_engagement,
        }

        # ======================================================
        # STEP 3: SCORE EACH CANDIDATE
        # ======================================================
        scored: List[Dict] = []

        for product_id in candidate_ids:
            # 1. POPULARITY — global trending (same for all sellers)
            popularity_score = popularity_map.get(product_id, 0.0)

            # 2. HISTORY — seller's past orders (strongest personalisation signal)
            #    If the seller never ordered this exact product, fall back to
            #    category affinity (discounted) so familiar categories are boosted.
            exact_history = (history.get(product_id) or {}).get("category_score") or 0.0
            cat_affinity  = affinity_data.get(product_id, 0.0)
            history_score = exact_history if exact_history > 0 else (cat_affinity * CATEGORY_AFFINITY_DISCOUNT)

            # 3. RECENCY — seller-scoped: when did THIS seller last order this product
            recency_score    = recency_data.get(product_id, {}).get("recency_score", 0.0)

            # 4. NEWNESS — product age (same for all sellers)
            newness_score    = newness_data.get(product_id, {}).get("newness_score", 0.0)

            # 5. ENGAGEMENT — global likes + comments
            engagement_score = engagement_data.get(product_id, {}).get("engagement_score", 0.0)

            final_score = (
                popularity_score  * weights["popularity"]  +
                history_score     * weights["history"]     +
                recency_score     * weights["recency"]     +
                newness_score     * weights["newness"]     +
                engagement_score  * weights["engagement"]
            )

            if final_score < self.settings.min_score_threshold:
                continue

            is_personalized = exact_history > 0 or recency_score > 0 or cat_affinity > 0

            scored.append({
                "product_id":      product_id,
                "score":           round(final_score, 2),
                "is_personalized": is_personalized,
                "sources": {
                    "popularity": round(popularity_score  * weights["popularity"],  2),
                    "history":    round(history_score     * weights["history"],     2),
                    "recency":    round(recency_score     * weights["recency"],     2),
                    "newness":    round(newness_score     * weights["newness"],     2),
                    "engagement": round(engagement_score  * weights["engagement"],  2),
                },
            })

        # ======================================================
        # STEP 4: RANK AND RETURN TOP N
        # ======================================================
        scored.sort(key=lambda x: (-x["score"], x["product_id"]))
        recommendations = scored[:limit]

        for i, rec in enumerate(recommendations, 1):
            rec["rank"] = i

        elapsed = time.perf_counter() - t_start

        # Emit Prometheus metric if available
        if _metrics_available:
            RECOMMENDATION_COMPUTE_DURATION_SECONDS.labels(
                cache_hit="false"
            ).observe(elapsed)

        personalized_count = sum(1 for r in recommendations if r["is_personalized"])
        logger.info(
            f"Generated {len(recommendations)} recommendations for seller {seller_id!r} "
            f"({personalized_count} personalized) — top score: "
            f"{recommendations[0]['score'] if recommendations else 0} "
            f"in {elapsed * 1000:.1f}ms"
        )

        return recommendations

    @staticmethod
    def _build_candidate_ids(
        popular: List[Dict],
        history: Dict[int, Dict],
        limit: int,
    ) -> List[int]:
        """
        Build a deterministic candidate pool.

        Popular products keep their DB ranking first, then seller-history-only
        products are appended in query order. This keeps ties stable for clients.
        """
        candidate_ids: List[int] = []
        seen: set[int] = set()

        for item in popular:
            product_id = item["product_id"]
            if product_id not in seen:
                candidate_ids.append(product_id)
                seen.add(product_id)

        for product_id in history.keys():
            if product_id not in seen:
                candidate_ids.append(product_id)
                seen.add(product_id)

        return candidate_ids[: limit * 2]
