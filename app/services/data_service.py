"""
Data Service Layer — Batch Database Queries

Provides all data fetching for the recommendation algorithm.
All queries are optimized:
- No N+1 patterns (batch operations)
- Proper JOINs and GROUP BY
- Seller-scoped signals where appropriate
- Only AVAILABLE products are ever surfaced
- Respects product visibility (isPublic + allowedSellerIds + minimumSellerType)
- Filters out products with zero stock in the seller's warehouse
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from sqlalchemy import func, and_, desc, or_, text
from sqlalchemy.orm import Session

from app.models import (
    Order, OrderItem, Product, ProductReaction, ProductComment,
    ProductVariantAssignment, WarehouseStock, User,
    SELLER_TYPE_HIERARCHY,
)
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
    - Only surfaces AVAILABLE products visible to the requesting seller
    - Enforces minimumSellerType tier restrictions
    - Filters out-of-stock products for the seller's warehouse
    """

    def __init__(
        self,
        db_session: Session,
        seller_id: Optional[str] = None,
        seller_type: Optional[str] = None,
        warehouse_id: Optional[int] = None,
    ):
        self.db = db_session
        self.settings = settings
        self._seller_id = seller_id
        self._seller_type = seller_type
        self._warehouse_id = warehouse_id

    def _get_seller_type(self) -> Optional[str]:
        """Resolve seller type — use cached value or fetch from DB."""
        if self._seller_type:
            return self._seller_type
        if not self._seller_id:
            return None
        user = self.db.query(User.sellerType).filter(User.id == self._seller_id).first()
        if user:
            self._seller_type = user[0] or "NORMAL"
        return self._seller_type

    def _product_visibility_filter(self, seller_id: Optional[str] = None):
        """
        Build a SQLAlchemy filter clause that enforces product visibility.

        A product is visible to a seller if:
        1. Product.status == "AVAILABLE"
        2. Product.isPublic == True OR seller's ID is in Product.allowedSellerIds
        3. Seller's type meets or exceeds Product.minimumSellerType

        This prevents restricted products from leaking into recommendations
        for unauthorized sellers.
        """
        sid = seller_id or self._seller_id
        base_filter = and_(
            Product.status == "AVAILABLE",
        )

        # Visibility: public or explicitly allowed
        if sid:
            visibility = or_(
                Product.isPublic == True,
                Product.allowedSellerIds.contains([sid]),
            )
        else:
            visibility = Product.isPublic == True

        # Seller type tier enforcement
        seller_type = self._get_seller_type()
        if seller_type:
            seller_tier = SELLER_TYPE_HIERARCHY.get(seller_type, 0)
            # Only show products where the seller's tier >= product's minimum tier
            # We filter by listing all allowed minimumSellerType values
            allowed_product_types = [
                ptype for ptype, tier in SELLER_TYPE_HIERARCHY.items()
                if tier <= seller_tier
            ]
            type_filter = Product.minimumSellerType.in_(allowed_product_types)
        else:
            # No seller type info — only show NORMAL products (safest default)
            type_filter = Product.minimumSellerType == "NORMAL"

        return and_(base_filter, visibility, type_filter)

    def _get_in_stock_product_ids(self, product_ids: List[int]) -> Set[int]:
        """
        Return the subset of product_ids that have available stock (quantity - reservedQuantity > 0)
        in the seller's warehouse.

        If no warehouse_id is configured, returns all product_ids (no filtering).
        """
        if not self._warehouse_id or not product_ids:
            return set(product_ids)

        try:
            results = (
                self.db.query(ProductVariantAssignment.productId)
                .join(
                    WarehouseStock,
                    WarehouseStock.productVariantAssignmentId == ProductVariantAssignment.id,
                )
                .filter(
                    and_(
                        ProductVariantAssignment.productId.in_(product_ids),
                        ProductVariantAssignment.isActive == True,
                        WarehouseStock.warehouseId == self._warehouse_id,
                        (WarehouseStock.quantity - WarehouseStock.reservedQuantity) > 0,
                    )
                )
                .distinct()
                .all()
            )
            in_stock = {row[0] for row in results}
            filtered_count = len(product_ids) - len(in_stock)
            if filtered_count > 0:
                logger.info(
                    f"Stock filter removed {filtered_count} out-of-stock products "
                    f"(warehouse {self._warehouse_id})"
                )
            return in_stock

        except Exception as e:
            logger.warning(f"Stock check failed, skipping filter: {e}")
            return set(product_ids)

    # ==================== POPULARITY SIGNALS ====================

    def get_popular_products(
        self,
        limit: int = 20,
        days: int = 90,
        seller_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get popular products based on recent order volume.

        Uses: Order count + quantity in specified time period.
        Scope: ALL sellers (global popularity).
        Filter: Only AVAILABLE products visible to the requesting seller.

        Args:
            limit: Maximum products to return
            days: Lookback period (default 90 days)
            seller_id: Seller requesting recommendations (for visibility filtering)

        Returns:
            List of dicts with product_id, order_count, total_quantity, score

        Query: 1 DB query (batch SELECT with GROUP BY + JOIN to Product)
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            sid = seller_id or self._seller_id

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
                    self._product_visibility_filter(sid),
                )
            ).group_by(
                OrderItem.productId
            ).order_by(
                desc(func.count(OrderItem.orderId))
            ).limit(limit).all()

            popular = []
            for product_id, order_count, total_qty in results:
                total_qty = total_qty or 0
                popular.append({
                    "product_id": product_id,
                    "order_count": int(order_count),
                    "total_quantity": int(total_qty),
                    "score": 0.0,  # placeholder, normalized below
                })

            # Rank-based normalization: top product = 100, bottom = scaled proportionally.
            # This adapts automatically regardless of absolute order volumes.
            if popular:
                max_orders = popular[0]["order_count"]  # results are ORDER BY desc
                max_qty = max((p["total_quantity"] for p in popular), default=1) or 1
                for item in popular:
                    order_score = (item["order_count"] / max_orders) * 100 if max_orders > 0 else 0
                    qty_score = (item["total_quantity"] / max_qty) * 100 if max_qty > 0 else 0
                    item["score"] = round((order_score * 0.6) + (qty_score * 0.4), 2)

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

        Filter: Only AVAILABLE products visible to the seller.

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
                    self._product_visibility_filter(seller_id),
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
        days: int = 180
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

        Uses an inverse-U reorder curve optimized for affiliate marketplace behavior:
        - Products ordered 0-7 days ago score LOW (seller just stocked up)
        - Products ordered 10-40 days ago score HIGH (restock window)
        - Products ordered 60+ days ago decay toward 0 (seller moved on)

        Peak reorder window: 15-30 days (score = 100)

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

                # Inverse-U reorder curve:
                # 0-7 days:   low (just bought, no need to reorder)  → ramps from 30 to 70
                # 8-14 days:  rising (approaching restock)           → ramps from 70 to 100
                # 15-40 days: peak reorder window                    → 100
                # 41-60 days: declining (might have switched)        → decays from 100 to 30
                # 61-90 days: low (likely moved on)                  → decays from 30 to 0
                # 90+ days:   zero
                if days_ago <= 7:
                    # Recently ordered — low urgency to reorder
                    score = 30.0 + (days_ago / 7.0) * 40.0
                elif days_ago <= 14:
                    # Approaching restock window
                    score = 70.0 + ((days_ago - 7) / 7.0) * 30.0
                elif days_ago <= 40:
                    # Peak reorder window
                    score = 100.0
                elif days_ago <= 60:
                    # Declining — seller might have switched
                    score = 100.0 - ((days_ago - 40) / 20.0) * 70.0
                elif days_ago <= 90:
                    # Low — likely moved on
                    score = 30.0 - ((days_ago - 60) / 30.0) * 30.0
                else:
                    score = 0.0

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

    # ==================== NEGATIVE SIGNALS (BATCH) - SELLER SCOPED ====================

    def get_negative_signals_batch(
        self,
        seller_id: str,
        product_ids: List[int],
        days: int = 180,
    ) -> Dict[int, Dict]:
        """
        Get negative signals for products: cancelled orders by this seller.

        A cancelled order indicates the seller tried to buy but backed out.
        This is a weak negative signal — it shouldn't completely suppress a product,
        but it should reduce its score.

        Penalty scale:
        - 1 cancellation:  penalty = 15
        - 2 cancellations: penalty = 30
        - 3+ cancellations: penalty = 50 (capped)

        Args:
            seller_id: Seller ID
            product_ids: Candidate product IDs
            days: Lookback window

        Returns:
            Dict mapping product_id -> {cancel_count, penalty_score}
            penalty_score is 0-50 (higher = more penalty to subtract)

        Query: 1 DB query
        """
        if not product_ids:
            return {}

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = self.db.query(
                OrderItem.productId,
                func.count(OrderItem.orderId).label("cancel_count"),
            ).join(
                Order, Order.id == OrderItem.orderId
            ).filter(
                and_(
                    OrderItem.productId.in_(product_ids),
                    Order.sellerId == seller_id,
                    Order.status == "CANCELLED",
                    Order.createdAt >= cutoff_date,
                )
            ).group_by(
                OrderItem.productId
            ).all()

            negative_data: Dict[int, Dict] = {}
            for product_id, cancel_count in results:
                cancel_count = cancel_count or 0
                # Diminishing penalty: 15 per cancel, capped at 50
                penalty = min(cancel_count * 15, 50)
                negative_data[product_id] = {
                    "cancel_count": int(cancel_count),
                    "penalty_score": float(penalty),
                }

            # Zero-fill products with no cancellations
            for pid in product_ids:
                if pid not in negative_data:
                    negative_data[pid] = {
                        "cancel_count": 0,
                        "penalty_score": 0.0,
                    }

            return negative_data

        except Exception as e:
            logger.error(f"Error getting negative signals: {str(e)}")
            return {pid: {"cancel_count": 0, "penalty_score": 0.0} for pid in product_ids}

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
        exclude_ids: List[int] = None,
        seller_id: Optional[str] = None,
    ) -> List[int]:
        """
        Fallback: fetch AVAILABLE products from the full catalog.

        Used when popularity + history candidate pool is smaller than requested
        limit. Products are ordered by rating (desc) then creation date (desc)
        so the highest-quality new products are prioritised.

        Args:
            limit: Max products to return
            exclude_ids: Product IDs already in the candidate pool
            seller_id: Seller requesting recommendations (for visibility filtering)

        Returns:
            List of product IDs

        Query: 1 DB query
        """
        if exclude_ids is None:
            exclude_ids = []

        try:
            sid = seller_id or self._seller_id
            query = self.db.query(Product.id).filter(
                self._product_visibility_filter(sid),
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

    # ==================== REFRESH TARGETING ====================

    def get_active_seller_ids(
        self,
        days: int = 30,
        limit: int = 500,
    ) -> List[str]:
        """
        Get recently active sellers for scheduled or catalog-wide refreshes.

        Activity is based on recent completed/confirmed order volume.
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            results = (
                self.db.query(
                    Order.sellerId,
                    func.count(Order.id).label("order_count"),
                    func.max(Order.createdAt).label("last_order_at"),
                )
                .filter(
                    and_(
                        Order.createdAt >= cutoff_date,
                        Order.status.in_(["CONFIRMED", "COMPLETED", "IN_DELIVERY", "PROCESSING"]),
                    )
                )
                .group_by(Order.sellerId)
                .order_by(
                    desc(func.count(Order.id)),
                    desc(func.max(Order.createdAt)),
                )
                .limit(limit)
                .all()
            )

            seller_ids = [row.sellerId for row in results if row.sellerId]
            logger.info(f"Found {len(seller_ids)} active sellers for refresh targeting")
            return seller_ids

        except Exception as e:
            logger.error(f"Error getting active seller ids: {str(e)}")
            return []
