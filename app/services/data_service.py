"""
Data Service Layer — Batch Database Queries

Provides all data fetching for the recommendation algorithm.
All queries are optimized:
- No N+1 patterns (batch operations)
- Proper JOINs and GROUP BY
- Seller-scoped signals where appropriate
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, and_, desc
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, Product, ProductReaction, ProductComment
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class DataService:
    """
    Database query layer for recommendations.

    All queries optimized for performance:
    - No N+1 patterns
    - Batch operations where possible
    - Proper JOINs and GROUP BY
    - Seller-scoped signals (not global)
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.settings = settings

    # ==================== POPULARITY SIGNALS ====================

    def get_popular_products(
        self,
        limit: int = 20,
        days: int = 30
    ) -> List[Dict]:
        """
        Get popular products based on recent order volume.

        Uses: Order count + quantity in specified time period.
        Scope: ALL sellers (global popularity).

        Args:
            limit: Maximum products to return
            days: Lookback period (default 30 days)

        Returns:
            List of dicts with product_id, order_count, total_quantity, score

        Query: 1 DB query (batch SELECT with GROUP BY)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                OrderItem.productId,
                func.count(OrderItem.orderId).label('order_count'),
                func.sum(OrderItem.quantity).label('total_quantity')
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
                )
            ).group_by(
                OrderItem.productId
            ).order_by(
                desc(func.count(OrderItem.orderId))
            ).limit(limit).all()

            popular = []
            for product_id, order_count, total_qty in results:
                # Popularity score: weighted combination of frequency and quantity
                order_score = min((order_count / 100) * 100, 100)
                qty_score = min((total_qty / 500) * 100, 100)
                score = (order_score * 0.6) + (qty_score * 0.4)

                popular.append({
                    'product_id': product_id,
                    'order_count': int(order_count),
                    'total_quantity': int(total_qty),
                    'score': round(score, 2)
                })

            logger.info(f"Found {len(popular)} popular products")
            return popular

        except Exception as e:
            logger.error(f"Error getting popular products: {str(e)}")
            return []

    # ==================== SELLER HISTORY SIGNALS ====================

    def get_seller_order_history(
        self,
        seller_id: str,
        days: int = 90,
        limit: int = 100
    ) -> Dict[int, Dict]:
        """
        Get seller's order history with category preferences.

        Args:
            seller_id: Seller ID
            days: Lookback period (default 90 days)
            limit: Max products to return

        Returns:
            Dict mapping product_id to {category_id, order_count, total_quantity, category_score}

        Query: 1 DB query (batch SELECT)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                Product.id,
                Product.categoryId,
                func.count(OrderItem.orderId).label('order_count'),
                func.sum(OrderItem.quantity).label('total_quantity')
            ).join(
                OrderItem, OrderItem.productId == Product.id
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    Order.sellerId == seller_id,
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
                )
            ).group_by(
                Product.id,
                Product.categoryId
            ).order_by(
                desc(func.count(OrderItem.orderId))
            ).limit(limit).all()

            history = {}
            for product_id, category_id, order_count, total_qty in results:
                score = min((order_count / 20) * 100, 100)
                qty_boost = min((total_qty / 200) * 20, 20)
                category_score = min(score + qty_boost, 100)

                history[product_id] = {
                    'category_id': category_id,
                    'order_count': int(order_count),
                    'total_quantity': int(total_qty),
                    'category_score': round(category_score, 2)
                }

            logger.info(f"Found {len(history)} products in seller history")
            return history

        except Exception as e:
            logger.error(f"Error getting seller history: {str(e)}")
            return {}

    # ==================== ENGAGEMENT SIGNALS (BATCH) ====================

    def get_engagement_scores_batch(
        self,
        product_ids: List[int],
        days: int = 30
    ) -> Dict[int, Dict]:
        """
        Get engagement scores for MULTIPLE products in ONE query.

        Fixes N+1 problem: Before 20 products = 20 queries
                          After 20 products = 1 query

        Args:
            product_ids: List of product IDs to score
            days: Lookback period for reactions/comments

        Returns:
            Dict mapping product_id to {reactions, comments, engagement_score}

        Query: 1 DB query (batch LEFT JOIN)
        """
        if not product_ids:
            return {}

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                OrderItem.productId,
                func.count(ProductReaction.id).label('reaction_count'),
                func.count(ProductComment.id).label('comment_count')
            ).outerjoin(
                ProductReaction,
                (ProductReaction.productId == OrderItem.productId) &
                (ProductReaction.createdAt >= cutoff_date)
            ).outerjoin(
                ProductComment,
                (ProductComment.productId == OrderItem.productId) &
                (ProductComment.createdAt >= cutoff_date)
            ).filter(
                OrderItem.productId.in_(product_ids)
            ).group_by(
                OrderItem.productId
            ).all()

            engagement_data = {}
            for product_id, reactions, comments in results:
                reaction_score = min((reactions / 100) * 100, 100) if reactions else 0
                comment_score = min((comments / 50) * 100, 100) if comments else 0
                engagement_score = (reaction_score * 0.7) + (comment_score * 0.3)

                engagement_data[product_id] = {
                    'reactions': int(reactions) if reactions else 0,
                    'comments': int(comments) if comments else 0,
                    'engagement_score': round(engagement_score, 2)
                }

            # Add missing products with zero engagement
            for pid in product_ids:
                if pid not in engagement_data:
                    engagement_data[pid] = {
                        'reactions': 0,
                        'comments': 0,
                        'engagement_score': 0.0
                    }

            return engagement_data

        except Exception as e:
            logger.error(f"Error getting engagement scores: {str(e)}")
            return {pid: {'reactions': 0, 'comments': 0, 'engagement_score': 0.0}
                    for pid in product_ids}

    # ==================== RECENCY SIGNALS (BATCH) - SELLER SCOPED ====================

    def get_recency_scores_batch(
        self,
        seller_id: str,
        product_ids: List[int]
    ) -> Dict[int, Dict]:
        """
        Get SELLER-SCOPED recency scores for MULTIPLE products.

        Uses seller-specific recency: when did THIS SELLER last order each product.
        Not global recency (which would be the same for all sellers).

        Args:
            seller_id: Seller ID for scoping
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {last_ordered_at, days_ago, recency_score}

        Query: 1 DB query (batch LEFT JOIN)
        """
        if not product_ids:
            return {}

        try:
            results = self.db.query(
                OrderItem.productId,
                func.max(Order.createdAt).label('last_ordered_at')
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    OrderItem.productId.in_(product_ids),
                    Order.sellerId == seller_id,
                    Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
                )
            ).group_by(
                OrderItem.productId
            ).all()

            recency_data = {}
            now = datetime.utcnow()

            for product_id, last_ordered in results:
                if last_ordered:
                    days_ago = (now - last_ordered).days
                else:
                    days_ago = 999

                # Piecewise linear decay: recent = high, old = low
                # 0 days ago = 100, 30 days = ~20, 90+ = 0
                if days_ago == 0:
                    score = 100.0
                elif days_ago <= 30:
                    score = 100 - (days_ago * 2.67)
                else:
                    score = max(0, 20 - ((days_ago - 30) * 0.22))

                recency_data[product_id] = {
                    'last_ordered_at': last_ordered,
                    'days_ago': days_ago,
                    'recency_score': round(min(max(score, 0), 100), 2)
                }

            # Add products never ordered by THIS seller
            for pid in product_ids:
                if pid not in recency_data:
                    recency_data[pid] = {
                        'last_ordered_at': None,
                        'days_ago': 999,
                        'recency_score': 0.0
                    }

            return recency_data

        except Exception as e:
            logger.error(f"Error getting recency scores: {str(e)}")
            return {pid: {'last_ordered_at': None, 'days_ago': 999, 'recency_score': 0.0}
                    for pid in product_ids}

    # ==================== NEWNESS SIGNALS (BATCH) ====================

    def get_newness_scores_batch(
        self,
        product_ids: List[int]
    ) -> Dict[int, Dict]:
        """
        Get newness scores for MULTIPLE products.

        Args:
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {created_at, days_old, newness_score}

        Query: 1 DB query (simple SELECT with WHERE IN)
        """
        if not product_ids:
            return {}

        try:
            results = self.db.query(
                Product.id,
                Product.createdAt
            ).filter(
                Product.id.in_(product_ids)
            ).all()

            newness_data = {}
            now = datetime.utcnow()

            for product_id, created_at in results:
                if created_at:
                    days_old = (now - created_at).days
                else:
                    days_old = 999

                # Linear decay: new = high, old = low
                # 0 days old = 100, 180 days old = 0
                score = max(0, 100 - ((days_old / 180) * 100))

                newness_data[product_id] = {
                    'created_at': created_at,
                    'days_old': days_old,
                    'newness_score': round(score, 2)
                }

            # Add missing products
            for pid in product_ids:
                if pid not in newness_data:
                    newness_data[pid] = {
                        'created_at': None,
                        'days_old': 999,
                        'newness_score': 0.0
                    }

            return newness_data

        except Exception as e:
            logger.error(f"Error getting newness scores: {str(e)}")
            return {pid: {'created_at': None, 'days_old': 999, 'newness_score': 0.0}
                    for pid in product_ids}

    # ==================== PRODUCT DETAILS ====================

    def get_product_details(
        self,
        product_ids: List[int]
    ) -> Dict[int, Dict]:
        """
        Get product details for recommendation display.

        Args:
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {name, code, price, category, rating}
        """
        if not product_ids:
            return {}

        try:
            results = self.db.query(
                Product.id,
                Product.name,
                Product.code,
                Product.sellingPrice,
                Product.categoryId,
                Product.ratingStars
            ).filter(
                Product.id.in_(product_ids)
            ).all()

            return {
                p.id: {
                    'name': p.name,
                    'code': p.code,
                    'selling_price': float(p.sellingPrice) if p.sellingPrice else 0,
                    'category_id': p.categoryId,
                    'rating_stars': float(p.ratingStars) if p.ratingStars else 0
                }
                for p in results
            }
        except Exception as e:
            logger.error(f"Error getting product details: {str(e)}")
            return {}
