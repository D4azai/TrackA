"""
Data service for fetching recommendation-relevant data from PostgreSQL
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging

from ..models import Order, OrderItem, Product, ProductReaction, ProductComment, SellerPreferences, Category
from ..config import settings

logger = logging.getLogger(__name__)


class DataService:
    """Service for querying recommendation data"""

    def __init__(self, db: Session):
        self.db = db

    def get_popular_products(
            self,
            seller_id: str,
            days: int = 30,
            limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get popular products based on order frequency and quantity (last N days)

        Scoring formula:
        - 60% weight on order frequency (how many orders)
        - 40% weight on total quantity (how much quantity)

        Args:
            seller_id: The seller requesting recommendations
            days: Number of days to look back (default 30)
            limit: Maximum products to return (default 50)

        Returns:
            List of dicts with structure:
            [
                {
                    'product_id': int,
                    'order_count': int,
                    'total_quantity': int,
                    'score': float (0-100)
                }
            ]
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Subquery: count orders and sum quantity per product
            # Include orders in relevant statuses (not RETURNED or NOT_CONFIRMED)
            result = self.db.query(
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

            # Normalize and score
            products = []
            for product_id, order_count, total_quantity in result:
                # Normalize to 0-100
                # Assuming max 100 orders and 500 quantity in 30 days for normalization
                order_score = min((order_count / 100) * 100, 100)
                quantity_score = min((total_quantity / 500) * 100, 100)

                # Hybrid score: 60% frequency, 40% quantity
                final_score = (order_score * 0.6) + (quantity_score * 0.4)

                products.append({
                    'product_id': product_id,
                    'order_count': order_count,
                    'total_quantity': total_quantity,
                    'score': round(final_score, 2)
                })

            logger.info(f"Found {len(products)} popular products for seller {seller_id}")
            return products

        except Exception as e:
            logger.error(f"Error getting popular products: {str(e)}")
            return []

    def get_seller_order_history(
            self,
            seller_id: str,
            days: int = 90
    ) -> Dict[int, Dict[str, Any]]:
        """
        Get seller's order history to identify preferred products

        Returns:
            Dict mapping product_id to {
                'category_id': int,
                'order_count': int,
                'total_quantity': int,
                'last_ordered_at': datetime
            }
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            result = self.db.query(
                Product.id,
                Product.categoryId,
                func.count(OrderItem.orderId).label('order_count'),
                func.sum(OrderItem.quantity).label('total_quantity'),
                func.max(Order.createdAt).label('last_ordered_at')
            ).join(
                Order, Order.id == OrderItem.orderId
            ).join(
                Product, Product.id == OrderItem.productId
            ).filter(
                and_(
                    Order.sellerId == seller_id,
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
                )
            ).group_by(
                Product.id,
                Product.categoryId
            ).all()

            history = {}
            for product_id, category_id, order_count, total_quantity, last_ordered in result:
                history[product_id] = {
                    'category_id': category_id,
                    'order_count': order_count,
                    'total_quantity': total_quantity,
                    'last_ordered_at': last_ordered
                }

            logger.info(f"Found {len(history)} previously ordered products for seller {seller_id}")
            return history

        except Exception as e:
            logger.error(f"Error getting seller order history: {str(e)}")
            return {}

    def get_seller_category_preferences(
            self,
            seller_id: str,
            days: int = 90
    ) -> Dict[int, float]:
        """
        Calculate category preference scores from order history

        Returns:
            Dict mapping category_id to score (0-100)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            result = self.db.query(
                Product.categoryId,
                func.count(OrderItem.orderId).label('order_count'),
                func.sum(OrderItem.quantity).label('total_quantity')
            ).join(
                Order, Order.id == OrderItem.orderId
            ).join(
                Product, Product.id == OrderItem.productId
            ).filter(
                and_(
                    Order.sellerId == seller_id,
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
                )
            ).group_by(
                Product.categoryId
            ).all()

            # Calculate scores normalized to 0-100
            category_scores = {}
            for category_id, order_count, total_quantity in result:
                # Score = (order_count * 0.7) + (quantity * 0.3)
                # Normalize both to 0-100 scale
                order_score = min((order_count / 50) * 100, 100)
                qty_score = min((total_quantity / 200) * 100, 100)

                score = (order_score * 0.7) + (qty_score * 0.3)
                category_scores[category_id] = round(score, 2)

            logger.info(f"Calculated categories for seller {seller_id}: {len(category_scores)} categories")
            return category_scores

        except Exception as e:
            logger.error(f"Error calculating category preferences: {str(e)}")
            return {}

    def get_engagement_score(
            self,
            product_id: int,
            days: int = 30
    ) -> float:
        """
        Calculate engagement score for a product (likes + comments)

        Returns:
            Score from 0-100
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            reactions = self.db.query(func.count(ProductReaction.id)).filter(
                ProductReaction.productId == product_id,
                ProductReaction.createdAt >= cutoff_date
            ).scalar() or 0

            comments = self.db.query(func.count(ProductComment.id)).filter(
                ProductComment.productId == product_id,
                ProductComment.createdAt >= cutoff_date
            ).scalar() or 0

            # Score: reactions weighted 70%, comments 30%
            reaction_score = min((reactions / 100) * 100, 100)
            comment_score = min((comments / 50) * 100, 100)

            score = (reaction_score * 0.7) + (comment_score * 0.3)
            return round(score, 2)

        except Exception as e:
            logger.error(f"Error calculating engagement score: {str(e)}")
            return 0.0

    def get_recency_score(
            self,
            product_id: int
    ) -> float:
        """
        Calculate recency score: how recently was this product ordered

        Returns:
            Score from 0-100 (100 = ordered today, 0 = not ordered in 90 days)
        """
        try:
            last_order = self.db.query(
                func.max(Order.createdAt)
            ).join(
                OrderItem, Order.id == OrderItem.orderId
            ).filter(
                OrderItem.productId == product_id,
                Order.status.in_(['CONFIRMED', 'COMPLETED', 'IN_DELIVERY', 'PROCESSING'])
            ).scalar()

            if not last_order:
                return 0.0

            days_ago = (datetime.utcnow() - last_order).days

            # Exponential decay: very recent = high score, older = lower
            # 0 days ago = 100, 1 day = 98, 7 days = 70, 30 days = 20, 90+ days = 0
            if days_ago == 0:
                score = 100
            elif days_ago <= 30:
                score = 100 - (days_ago * 2.67)  # 30 days = ~20 points
            else:
                score = max(0, 20 - ((days_ago - 30) * 0.22))  # Gradual decay after 30 days

            return round(min(max(score, 0), 100), 2)

        except Exception as e:
            logger.error(f"Error calculating recency score: {str(e)}")
            return 0.0

    def get_newness_score(
            self,
            product_id: int
    ) -> float:
        """
        Calculate newness score: how recently was this product added

        Returns:
            Score from 0-100 (100 = added today, 0 = added 180+ days ago)
        """
        try:
            product = self.db.query(Product).filter(Product.id == product_id).first()

            if not product:
                return 0.0

            days_old = (datetime.utcnow() - product.createdAt).days

            # Decay over 6 months
            # 0 days old = 100, 30 days = 80, 90 days = 40, 180+ days = 0
            if days_old <= 30:
                score = 100 - (days_old * 0.67)  # Steep decay
            elif days_old <= 90:
                score = 80 - ((days_old - 30) * 1.33)  # Medium decay
            else:
                score = max(0, 40 - ((days_old - 90) * 0.22))  # Gradual decay

            return round(min(max(score, 0), 100), 2)

        except Exception as e:
            logger.error(f"Error calculating newness score: {str(e)}")
            return 0.0

    def get_product_details(
            self,
            product_ids: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Fetch product details needed for final recommendations

        Returns:
            Dict mapping product_id to {
                'name': str,
                'code': str,
                'category_id': int,
                'selling_price': float,
                'rating_stars': int,
                'is_public': bool
            }
        """
        try:
            if not product_ids:
                return {}

            results = self.db.query(Product).filter(
                Product.id.in_(product_ids)
            ).all()

            details = {}
            for product in results:
                details[product.id] = {
                    'name': product.name,
                    'code': product.code,
                    'category_id': product.categoryId,
                    'selling_price': product.sellingPrice,
                    'rating_stars': product.ratingStars,
                    'is_public': product.isPublic
                }

            return details

        except Exception as e:
            logger.error(f"Error fetching product details: {str(e)}")
            return {}

    def get_seller_preferences_from_db(
            self,
            seller_id: str
    ) -> Dict[str, Any]:
        """
        Fetch precomputed seller preferences from database

        Returns:
            SellerPreferences dict or empty dict if not found
        """
        try:
            prefs = self.db.query(SellerPreferences).filter(
                SellerPreferences.sellerId == seller_id
            ).first()

            if not prefs:
                logger.warning(f"No preferences found for seller {seller_id}")
                return {}

            return {
                'category_scores': prefs.categoryScores,
                'price_range_min': prefs.priceRangeMin,
                'price_range_max': prefs.priceRangeMax,
                'total_orders': prefs.totalOrders,
                'computed_at': prefs.computedAt
            }

        except Exception as e:
            logger.error(f"Error fetching seller preferences: {str(e)}")
            return {}
