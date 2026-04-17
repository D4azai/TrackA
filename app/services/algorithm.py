"""
Recommendation Algorithm — Production Ready

Weighted ensemble of 5 signals:
1. Popularity (25%): Global trending products
2. History (35%): Seller's past orders (strongest signal)
3. Recency (20%): When seller last ordered (seller-scoped)
4. Newness (15%): Product age
5. Engagement (5%): Likes and comments

Performance: ~5 database queries total
Response time target: <200ms
"""

import logging
from typing import List, Dict

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.data_service import DataService

logger = logging.getLogger(__name__)
settings = get_settings()


class RecommendationEngine:
    """
    Production recommendation engine.

    Algorithm: Weighted ensemble of 5 signals
    - Popularity (25%): Global trending products
    - History (35%): Seller's past orders (strongest signal)
    - Recency (20%): When seller last ordered (seller-scoped)
    - Newness (15%): Product age
    - Engagement (5%): Likes and comments
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.data_service = DataService(db_session)
        self.settings = settings

    def compute_recommendations(
        self,
        seller_id: str,
        limit: int = 30
    ) -> List[Dict]:
        """
        Compute recommendations for a seller.

        Args:
            seller_id: Seller making request
            limit: Number of recommendations (max 100)

        Returns:
            List of recommendations with product_id, score, rank, and signal breakdown

        Database queries:
        1. get_popular_products()
        2. get_seller_order_history()
        3. get_engagement_scores_batch()
        4. get_recency_scores_batch()
        5. get_newness_scores_batch()

        Total: 5 queries per request
        """
        logger.info(f"Computing recommendations for seller {seller_id}, limit={limit}")

        try:
            # Validate limit
            limit = min(limit, self.settings.max_limit)
            limit = max(limit, 1)

            # ========== STEP 1: GATHER CANDIDATES ==========
            popular = self.data_service.get_popular_products(
                limit=limit * 2  # Get 2x to have enough after filtering
            )

            history = self.data_service.get_seller_order_history(
                seller_id,
                limit=limit * 2
            )

            # Build popularity lookup dict for O(1) access (instead of O(n) linear scan)
            popularity_map = {
                item['product_id']: item['score']
                for item in popular
            }

            # Combine: products from both sources
            candidate_ids = set()
            for item in popular:
                candidate_ids.add(item['product_id'])
            for product_id in history.keys():
                candidate_ids.add(product_id)

            candidate_ids = list(candidate_ids)[:limit * 2]

            if not candidate_ids:
                logger.warning(f"No candidate products found for seller {seller_id}")
                return []

            logger.info(f"Collected {len(candidate_ids)} candidate products")

            # ========== STEP 2: BATCH SCORE ALL SIGNALS ==========
            engagement_data = self.data_service.get_engagement_scores_batch(candidate_ids)
            recency_data = self.data_service.get_recency_scores_batch(seller_id, candidate_ids)
            newness_data = self.data_service.get_newness_scores_batch(candidate_ids)

            # ========== STEP 3: SCORE EACH PRODUCT ==========
            weights = {
                'popularity': self.settings.weight_popularity,
                'history': self.settings.weight_history,
                'recency': self.settings.weight_recency,
                'newness': self.settings.weight_newness,
                'engagement': self.settings.weight_engagement
            }
            scored_products = []

            for product_id in candidate_ids:
                # 1. POPULARITY (25%): Global trending — O(1) dict lookup
                popularity_score = popularity_map.get(product_id, 0.0)

                # 2. HISTORY (35%): Seller's past orders (strongest signal)
                history_score = history.get(product_id, {}).get('category_score', 0.0)

                # 3. RECENCY (20%): When seller last ordered (seller-scoped)
                recency_score = recency_data[product_id]['recency_score']

                # 4. NEWNESS (15%): Product age
                newness_score = newness_data[product_id]['newness_score']

                # 5. ENGAGEMENT (5%): Likes and comments
                engagement_score = engagement_data[product_id]['engagement_score']

                # Weighted sum
                final_score = (
                    popularity_score * weights['popularity'] +
                    history_score * weights['history'] +
                    recency_score * weights['recency'] +
                    newness_score * weights['newness'] +
                    engagement_score * weights['engagement']
                )

                # Apply score threshold
                if final_score >= self.settings.min_score_threshold:
                    scored_products.append({
                        'product_id': product_id,
                        'score': round(final_score, 2),
                        'sources': {
                            'popularity': round(popularity_score * weights['popularity'], 2),
                            'history': round(history_score * weights['history'], 2),
                            'recency': round(recency_score * weights['recency'], 2),
                            'newness': round(newness_score * weights['newness'], 2),
                            'engagement': round(engagement_score * weights['engagement'], 2)
                        }
                    })

            # ========== STEP 4: RANK AND RETURN TOP N ==========
            scored_products.sort(key=lambda x: x['score'], reverse=True)
            recommendations = scored_products[:limit]

            # Add ranking
            for i, rec in enumerate(recommendations, 1):
                rec['rank'] = i

            logger.info(
                f"Generated {len(recommendations)} recommendations for seller {seller_id}, "
                f"top score: {recommendations[0]['score'] if recommendations else 0}"
            )

            return recommendations

        except Exception as e:
            logger.error(f"Error computing recommendations: {str(e)}", exc_info=True)
            return []
