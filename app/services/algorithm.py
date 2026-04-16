"""
Core recommendation algorithm
Hybrid scoring approach combining multiple signals
"""

from sqlalchemy.orm import Session
from typing import List, Dict, Any
import logging

from .data_service import DataService
from ..config import settings

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Main recommendation algorithm"""

    def __init__(self, db: Session):
        self.db = db
        self.data_service = DataService(db)
        self.config = settings

    async def compute_recommendations(
            self,
            seller_id: str,
            limit: int = 20,
            exclude_product_ids: List[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Compute product recommendations for a seller

        Hybrid algorithm combining:
        - Popularity (25%): What sells fast
        - History (35%): What seller ordered before
        - Engagement (5%): Likes/comments
        - Recency (20%): Recently ordered products
        - Newness (15%): Recently added products

        Args:
            seller_id: The seller requesting recommendations
            limit: Number of products to return (default 20)
            exclude_product_ids: Products to exclude (already in cart, etc.)

        Returns:
            List of recommended products with scores, sorted by score descending
            [
                {
                    'product_id': int,
                    'score': float (0-100),
                    'rank': int,
                    'sources': {
                        'popularity': float,
                        'history': float,
                        'engagement': float,
                        'recency': float,
                        'newness': float
                    }
                }
            ]
        """
        try:
            exclude_ids = exclude_product_ids or []

            logger.info(f"Computing recommendations for seller {seller_id}, limit={limit}")

            # --- Phase 1: Fetch data ---
            popular_products = self.data_service.get_popular_products(
                seller_id,
                days=30,
                limit=100  # Get more candidates
            )

            seller_history = self.data_service.get_seller_order_history(seller_id, days=90)

            # Collect all candidate product IDs
            all_product_ids = list(set(
                [p['product_id'] for p in popular_products] +
                list(seller_history.keys())
            ))

            # Remove excluded products
            candidate_ids = [p for p in all_product_ids if p not in exclude_ids]

            logger.info(f"Collected {len(candidate_ids)} candidate products")

            # --- Phase 2: Calculate scores for each product ---
            scored_products = []

            for product_id in candidate_ids:
                # Initialize scores
                popularity_score = 0.0
                history_score = 0.0
                engagement_score = 0.0
                recency_score = 0.0
                newness_score = 0.0

                # Popularity: from popular_products list
                popular_item = next(
                    (p for p in popular_products if p['product_id'] == product_id),
                    None
                )
                if popular_item:
                    popularity_score = popular_item['score']

                # History: if seller ordered it before
                if product_id in seller_history:
                    history_item = seller_history[product_id]
                    # Score based on order frequency (0-100)
                    # Normalize: assume max 20 orders in history
                    history_score = min((history_item['order_count'] / 20) * 100, 100)

                # Engagement: likes and comments
                engagement_score = self.data_service.get_engagement_score(product_id)

                # Recency: when was it last ordered
                recency_score = self.data_service.get_recency_score(product_id)

                # Newness: when was it added
                newness_score = self.data_service.get_newness_score(product_id)

                # --- Phase 3: Weighted hybrid score ---
                final_score = (
                        (popularity_score * self.config.weight_popularity) +
                        (history_score * self.config.weight_history) +
                        (engagement_score * self.config.weight_engagement) +
                        (recency_score * self.config.weight_recency) +
                        (newness_score * self.config.weight_newness)
                )

                # Normalize to 0-100
                final_score = min(max(final_score, 0), 100)

                scored_products.append({
                    'product_id': product_id,
                    'score': round(final_score, 2),
                    'sources': {
                        'popularity': round(popularity_score, 2),
                        'history': round(history_score, 2),
                        'engagement': round(engagement_score, 2),
                        'recency': round(recency_score, 2),
                        'newness': round(newness_score, 2)
                    }
                })

            # --- Phase 4: Sort and limit ---
            scored_products.sort(key=lambda x: x['score'], reverse=True)
            recommendations = scored_products[:limit]

            # Add rank
            for idx, rec in enumerate(recommendations, 1):
                rec['rank'] = idx

            logger.info(
                f"Generated {len(recommendations)} recommendations for seller {seller_id}, "
                f"top score: {recommendations[0]['score'] if recommendations else 0}"
            )

            return recommendations

        except Exception as e:
            logger.error(f"Error computing recommendations: {str(e)}")
            raise

    def filter_by_seller_visibility(
            self,
            products: List[Dict[str, Any]],
            seller_id: str
    ) -> List[Dict[str, Any]]:
        """
        Filter products by visibility rules

        - Only public products (isPublic=True)
        - Only products seller is allowed to buy
        - Only AVAILABLE status

        Args:
            products: Recommended products with details
            seller_id: The requesting seller

        Returns:
            Filtered products list
        """
        try:
            product_ids = [p['product_id'] for p in products]
            product_details = self.data_service.get_product_details(product_ids)

            filtered = []
            for product in products:
                details = product_details.get(product['product_id'])

                if not details:
                    continue

                # Check visibility rules
                if not details['is_public']:
                    continue

                # Check if seller is allowed (allowedSellerIds is empty = all allowed)
                allowed_sellers = product.get('allowed_seller_ids', [])
                if allowed_sellers and seller_id not in allowed_sellers:
                    continue

                filtered.append(product)

            logger.info(f"Filtered {len(products)} products → {len(filtered)} visible to seller {seller_id}")
            return filtered

        except Exception as e:
            logger.error(f"Error filtering by visibility: {str(e)}")
            return products  # Return unfiltered on error

    def add_product_details(
            self,
            products: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich recommendations with product details

        Args:
            products: Recommended products (requires product_id)

        Returns:
            Products with added details: name, code, category_id, price, rating
        """
        try:
            product_ids = [p['product_id'] for p in products]
            details = self.data_service.get_product_details(product_ids)

            enriched = []
            for product in products:
                detail = details.get(product['product_id'])
                if detail:
                    product.update({
                        'name': detail['name'],
                        'code': detail['code'],
                        'category_id': detail['category_id'],
                        'selling_price': detail['selling_price'],
                        'rating_stars': detail['rating_stars']
                    })
                    enriched.append(product)

            return enriched

        except Exception as e:
            logger.error(f"Error adding product details: {str(e)}")
            return products
