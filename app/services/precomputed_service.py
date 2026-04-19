"""
Durable PostgreSQL-backed recommendation snapshots.
"""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RecommendationRefreshJob, SellerRecommendation

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class PrecomputedRecommendationSnapshot:
    """Latest durable recommendation snapshot for a seller."""

    seller_id: str
    recommendations: List[Dict]
    computed_at: Optional[datetime]
    algorithm_version: Optional[str]
    age_seconds: Optional[float]
    is_fresh: bool


class PrecomputedRecommendationService:
    """Read and write durable recommendation snapshots in PostgreSQL."""

    def __init__(self, db_session: Session):
        self.db = db_session
        self.settings = settings

    def get_latest_snapshot(
        self,
        seller_id: str,
        limit: int,
    ) -> Optional[PrecomputedRecommendationSnapshot]:
        """
        Fetch the latest stored recommendations for a seller.

        If a seller recently completed a refresh job with zero results, the latest
        completed job still acts as a fresh empty snapshot.
        """
        rows = (
            self.db.query(SellerRecommendation)
            .filter(SellerRecommendation.sellerId == seller_id)
            .order_by(SellerRecommendation.rank.asc())
            .limit(limit)
            .all()
        )

        if rows:
            computed_at = rows[0].computedAt
            algorithm_version = rows[0].algorithmVersion
            recommendations = [
                {
                    "product_id": row.productId,
                    "score": round(float(row.score), 2),
                    "rank": row.rank,
                    "is_personalized": bool(row.isPersonalized),
                    "sources": row.sources or {},
                }
                for row in rows
            ]
            age_seconds = self._age_seconds(computed_at)
            return PrecomputedRecommendationSnapshot(
                seller_id=seller_id,
                recommendations=recommendations,
                computed_at=computed_at,
                algorithm_version=algorithm_version,
                age_seconds=age_seconds,
                is_fresh=self._is_fresh(age_seconds),
            )

        latest_completed_job = (
            self.db.query(RecommendationRefreshJob)
            .filter(
                RecommendationRefreshJob.sellerId == seller_id,
                RecommendationRefreshJob.status == "COMPLETED",
            )
            .order_by(RecommendationRefreshJob.completedAt.desc())
            .first()
        )
        if latest_completed_job and latest_completed_job.completedAt:
            age_seconds = self._age_seconds(latest_completed_job.completedAt)
            return PrecomputedRecommendationSnapshot(
                seller_id=seller_id,
                recommendations=[],
                computed_at=latest_completed_job.completedAt,
                algorithm_version=latest_completed_job.algorithmVersion,
                age_seconds=age_seconds,
                is_fresh=self._is_fresh(age_seconds),
            )

        return None

    def replace_seller_recommendations(
        self,
        seller_id: str,
        recommendations: List[Dict],
        computed_at: datetime,
        algorithm_version: Optional[str] = None,
    ) -> None:
        """Atomically replace the latest durable snapshot for one seller."""
        self.db.query(SellerRecommendation).filter(
            SellerRecommendation.sellerId == seller_id
        ).delete(synchronize_session=False)

        version = algorithm_version or self.settings.recommendation_algorithm_version
        snapshot_rows = [
            SellerRecommendation(
                sellerId=seller_id,
                productId=item["product_id"],
                score=float(item["score"]),
                rank=int(item["rank"]),
                isPersonalized=bool(item.get("is_personalized", False)),
                sources=item.get("sources") or {},
                computedAt=computed_at,
                algorithmVersion=version,
            )
            for item in recommendations
        ]
        if snapshot_rows:
            self.db.add_all(snapshot_rows)

        logger.info(
            "Stored %s precomputed recommendations for seller %r at %s",
            len(recommendations),
            seller_id,
            computed_at.isoformat(),
        )

    def _age_seconds(self, computed_at: datetime) -> float:
        return max((datetime.utcnow() - computed_at).total_seconds(), 0.0)

    def _is_fresh(self, age_seconds: Optional[float]) -> bool:
        return age_seconds is not None and age_seconds <= self.settings.precomputed_freshness_seconds
