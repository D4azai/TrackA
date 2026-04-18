"""
Data Service Layer — Batch Database Queries

Provides all data fetching for the recommendation algorithm.
All queries are optimized:
- No N+1 patterns (batch operations)
- Proper JOINs and GROUP BY
- Seller-scoped signals where appropriate
- Only AVAILABLE products are ever surfaced
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, and_, desc, text
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
    - Only surfaces AVAILABLE products
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.settings = settings

    # ==================== POPULARITY SIGNALS ====================

    def get_popular_products(
        self,
        limit: int = 20,
        days: int = 90
    ) -> List[Dict]:
        """
        Get popular products based on recent order volume.

        Uses: Order count + quantity in specified time period.
        Scope: ALL sellers (global popularity).
        Filter: Only AVAILABLE products.

        Args:
            limit: Maximum products to return
            days: Lookback period (default 90 days)

        Returns:
            List of dicts with product_id, order_count, total_quantity, score

        Query: 1 DB query (batch SELECT with GROUP BY + JOIN to Product)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                OrderItem.productId,
                func.count(OrderItem.orderId).label("order_count"),
                func.sum(OrderItem.quantity).label("total_quantity")
            ).join(
                Order, Order.id == OrderItem.orderId
            ).join(
                Product, Product.id == OrderItem.productId
            ).filter(
                and_(
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(["CONFIRMED", "COMPLETED", "IN_DELIVERY", "PROCESSING"]),
                    Product.status == "AVAILABLE",
                    Product.isPublic == True,
                )
            ).group_by(
                OrderItem.productId
            ).order_by(
                desc(func.count(OrderItem.orderId))
            ).limit(limit).all()

            popular = []
            for product_id, order_count, total_qty in results:
                total_qty = total_qty or 0
                order_score = min((order_count / 100) * 100, 100)
                qty_score = min((total_qty / 500) * 100, 100)
                score = (order_score * 0.6) + (qty_score * 0.4)

                popular.append({
                    "product_id": product_id,
                    "order_count": int(order_count),
                    "total_quantity": int(total_qty),
                    "score": round(score, 2),
                })

            logger.info(f"Found {len(popular)} popular products (last {days}d)")
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

        Filter: Only AVAILABLE products (avoids recommending things no longer for sale).

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
                func.count(OrderItem.orderId).label("order_count"),
                func.sum(OrderItem.quantity).label("total_quantity")
            ).join(
                OrderItem, OrderItem.productId == Product.id
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    Order.sellerId == seller_id,
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(["CONFIRMED", "COMPLETED", "IN_DELIVERY", "PROCESSING"]),
                    Product.status == "AVAILABLE",
                )
            ).group_by(
                Product.id,
                Product.categoryId
            ).order_by(
                desc(func.count(OrderItem.orderId))
            ).limit(limit).all()

            history = {}
            for product_id, category_id, order_count, total_qty in results:
                total_qty = total_qty or 0
                score = min((order_count / 20) * 100, 100)
                qty_boost = min((total_qty / 200) * 20, 20)
                category_score = min(score + qty_boost, 100)

                history[product_id] = {
                    "category_id": category_id,
                    "order_count": int(order_count),
                    "total_quantity": int(total_qty),
                    "category_score": round(category_score, 2),
                }

            logger.info(f"Found {len(history)} products in seller history")
            return history

        except Exception as e:
            logger.error(f"Error getting seller history: {str(e)}")
            return {}

    # ==================== CATEGORY AFFINITY SIGNALS ====================

    def get_category_affinity_scores(
        self,
        seller_id: str,
        product_ids: List[int],
        days: int = 90
    ) -> Dict[int, float]:
        """
        Compute category-level affinity score for each candidate product.

        How it works:
          1. Count how many orders the seller has placed per category.
          2. For each candidate product, look up its category.
          3. Return a 0-100 score proportional to the seller's activity in
             that category — even if they never ordered THAT EXACT product.

        This bridges the cold-start gap: a seller who often orders electronics
        will get electronics products boosted, even brand-new ones.

        Args:
            seller_id: Seller ID
            product_ids: Candidate product IDs to score
            days: Lookback window for seller orders

        Returns:
            Dict mapping product_id -> category_affinity_score (0-100)

        Queries: 2 DB queries (category preferences + product categories)
        """
        if not product_ids:
            return {}

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Query 1: seller's category order counts
            cat_results = self.db.query(
                Product.categoryId,
                func.count(OrderItem.orderId).label("order_count"),
                func.sum(OrderItem.quantity).label("total_qty")
            ).join(
                OrderItem, OrderItem.productId == Product.id
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    Order.sellerId == seller_id,
                    Order.createdAt >= cutoff_date,
                    Order.status.in_(["CONFIRMED", "COMPLETED", "IN_DELIVERY", "PROCESSING"]),
                )
            ).group_by(
                Product.categoryId
            ).all()

            if not cat_results:
                # New seller — no affinity data
                return {pid: 0.0 for pid in product_ids}

            # Build category -> score map (0-100, normalised to top category = 100)
            cat_order_counts = {
                row.categoryId: (row.order_count or 0)
                for row in cat_results
            }
            max_count = max(cat_order_counts.values()) or 1
            category_score_map = {
                cat_id: round((count / max_count) * 100, 2)
                for cat_id, count in cat_order_counts.items()
            }

            # Query 2: category for each candidate product
            prod_cats = self.db.query(
                Product.id,
                Product.categoryId,
            ).filter(
                Product.id.in_(product_ids)
            ).all()

            affinity: Dict[int, float] = {}
            for product_id, category_id in prod_cats:
                affinity[product_id] = category_score_map.get(category_id, 0.0)

            # Zero-fill any missing
            for pid in product_ids:
                if pid not in affinity:
                    affinity[pid] = 0.0

            return affinity

        except Exception as e:
            logger.error(f"Error getting category affinity scores: {str(e)}")
            return {pid: 0.0 for pid in product_ids}

    # ==================== ENGAGEMENT SIGNALS (BATCH) ====================

    def get_engagement_scores_batch(
        self,
        product_ids: List[int],
        days: int = 30
    ) -> Dict[int, Dict]:
        """
        Get engagement scores for MULTIPLE products in ONE query.

        Base table is Product (not OrderItem) so products with reactions/comments
        but no recent orders are correctly included.

        Args:
            product_ids: List of product IDs to score
            days: Lookback period for reactions/comments

        Returns:
            Dict mapping product_id to {reactions, comments, engagement_score}

        Query: 1 DB query (batch LEFT JOIN from Product)
        """
        if not product_ids:
            return {}

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                Product.id,
                func.count(ProductReaction.id.distinct()).label("reaction_count"),
                func.count(ProductComment.id.distinct()).label("comment_count"),
            ).outerjoin(
                ProductReaction,
                and_(
                    ProductReaction.productId == Product.id,
                    ProductReaction.createdAt >= cutoff_date,
                )
            ).outerjoin(
                ProductComment,
                and_(
                    ProductComment.productId == Product.id,
                    ProductComment.createdAt >= cutoff_date,
                )
            ).filter(
                Product.id.in_(product_ids)
            ).group_by(
                Product.id
            ).all()

            engagement_data = {}
            for product_id, reactions, comments in results:
                reactions = reactions or 0
                comments = comments or 0
                reaction_score = min((reactions / 100) * 100, 100)
                comment_score = min((comments / 50) * 100, 100)
                engagement_score = (reaction_score * 0.7) + (comment_score * 0.3)

                engagement_data[product_id] = {
                    "reactions": int(reactions),
                    "comments": int(comments),
                    "engagement_score": round(engagement_score, 2),
                }

            # Zero-fill missing
            for pid in product_ids:
                if pid not in engagement_data:
                    engagement_data[pid] = {
                        "reactions": 0,
                        "comments": 0,
                        "engagement_score": 0.0,
                    }

            return engagement_data

        except Exception as e:
            logger.error(f"Error getting engagement scores: {str(e)}")
            return {pid: {"reactions": 0, "comments": 0, "engagement_score": 0.0}
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

        Args:
            seller_id: Seller ID for scoping
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {last_ordered_at, days_ago, recency_score}

        Query: 1 DB query
        """
        if not product_ids:
            return {}

        try:
            results = self.db.query(
                OrderItem.productId,
                func.max(Order.createdAt).label("last_ordered_at")
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    OrderItem.productId.in_(product_ids),
                    Order.sellerId == seller_id,
                    Order.status.in_(["CONFIRMED", "COMPLETED", "IN_DELIVERY", "PROCESSING"]),
                )
            ).group_by(
                OrderItem.productId
            ).all()

            recency_data = {}
            now = datetime.utcnow()

            for product_id, last_ordered in results:
                days_ago = (now - last_ordered).days if last_ordered else 999

                # Piecewise linear decay:
                # 0 days ago → 100,  30 days → ~20,  90+ days → 0
                if days_ago == 0:
                    score = 100.0
                elif days_ago <= 30:
                    score = 100 - (days_ago * 2.67)
                else:
                    score = max(0.0, 20 - ((days_ago - 30) * 0.22))

                recency_data[product_id] = {
                    "last_ordered_at": last_ordered,
                    "days_ago": days_ago,
                    "recency_score": round(min(max(score, 0), 100), 2),
                }

            # Products never ordered by this seller
            for pid in product_ids:
                if pid not in recency_data:
                    recency_data[pid] = {
                        "last_ordered_at": None,
                        "days_ago": 999,
                        "recency_score": 0.0,
                    }

            return recency_data

        except Exception as e:
            logger.error(f"Error getting recency scores: {str(e)}")
            return {pid: {"last_ordered_at": None, "days_ago": 999, "recency_score": 0.0}
                    for pid in product_ids}

    # ==================== NEWNESS SIGNALS (BATCH) ====================

    def get_newness_scores_batch(
        self,
        product_ids: List[int]
    ) -> Dict[int, Dict]:
        """
        Get newness scores for MULTIPLE products.

        Linear decay: 0 days old → 100,  180 days old → 0.

        Args:
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {created_at, days_old, newness_score}

        Query: 1 DB query
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
                days_old = (now - created_at).days if created_at else 999
                score = max(0.0, 100 - ((days_old / 180) * 100))

                newness_data[product_id] = {
                    "created_at": created_at,
                    "days_old": days_old,
                    "newness_score": round(score, 2),
                }

            for pid in product_ids:
                if pid not in newness_data:
                    newness_data[pid] = {
                        "created_at": None,
                        "days_old": 999,
                        "newness_score": 0.0,
                    }

            return newness_data

        except Exception as e:
            logger.error(f"Error getting newness scores: {str(e)}")
            return {pid: {"created_at": None, "days_old": 999, "newness_score": 0.0}
                    for pid in product_ids}

    # ==================== CATALOG FALLBACK ====================

    def get_catalog_fallback_products(
        self,
        limit: int,
        exclude_ids: List[int] = None
    ) -> List[int]:
        """
        Fallback: fetch AVAILABLE products from the full catalog.

        Used when popularity + history candidate pool is smaller than requested
        limit. Products are ordered by rating (desc) then creation date (desc)
        so the highest-quality new products are prioritised.

        Args:
            limit: Max products to return
            exclude_ids: Product IDs already in the candidate pool

        Returns:
            List of product IDs

        Query: 1 DB query
        """
        if exclude_ids is None:
            exclude_ids = []

        try:
            query = self.db.query(Product.id).filter(
                Product.status == "AVAILABLE",
                Product.isPublic == True,
            )
            if exclude_ids:
                query = query.filter(Product.id.notin_(exclude_ids))

            results = query.order_by(
                desc(Product.ratingStars),
                desc(Product.createdAt),
            ).limit(limit).all()

            ids = [row[0] for row in results]
            logger.info(f"Catalog fallback returned {len(ids)} products")
            return ids

        except Exception as e:
            logger.error(f"Error getting catalog fallback products: {str(e)}")
            return []

    # ==================== PRODUCT DETAILS ====================

    def get_product_details(
        self,
        product_ids: List[int]
    ) -> Dict[int, Dict]:
        """
        Get product details for recommendation enrichment.

        Args:
            product_ids: List of product IDs

        Returns:
            Dict mapping product_id to {name, code, price, category_id, rating_stars}
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
                Product.ratingStars,
            ).filter(
                Product.id.in_(product_ids)
            ).all()

            return {
                p.id: {
                    "name": p.name,
                    "code": p.code,
                    "selling_price": float(p.sellingPrice) if p.sellingPrice else 0.0,
                    "category_id": p.categoryId,
                    "rating_stars": float(p.ratingStars) if p.ratingStars else 0.0,
                }
                for p in results
            }

        except Exception as e:
            logger.error(f"Error getting product details: {str(e)}")
            return {}
