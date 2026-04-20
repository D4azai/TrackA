"""
Background refresh orchestration for recommendations.
"""

from dataclasses import dataclass
from datetime import datetime
import logging
import time
import json
from typing import Dict, Iterable, List, Optional

try:
    from kafka import KafkaProducer
except ImportError:
    KafkaProducer = None

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RecommendationRefreshJob
from app.services.algorithm import RecommendationEngine
from app.services.cache_service import CacheService
from app.services.data_service import DataService
from app.services.precomputed_service import PrecomputedRecommendationService

try:
    from app.metrics import (
        RECOMMENDATION_REFRESH_DURATION_SECONDS,
        RECOMMENDATION_REFRESH_JOBS_TOTAL,
    )

    _metrics_available = True
except ImportError:
    _metrics_available = False

logger = logging.getLogger(__name__)
settings = get_settings()

# Jobs that fail fewer than this many times are re-queued instead of
# permanently marked FAILED. Override with REFRESH_MAX_ATTEMPTS in .env.
_MAX_ATTEMPTS: int = settings.refresh_max_attempts

_kafka_producer = None

def get_kafka_producer():
    global _kafka_producer
    if _kafka_producer is None and KafkaProducer is not None:
        try:
            _kafka_producer = KafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Failed to connect to Kafka: {e}")
    return _kafka_producer


@dataclass
class RefreshEnqueueResult:
    job_id: Optional[int]
    created: bool
    status: str


@dataclass
class RefreshRunSummary:
    processed: int
    succeeded: int
    failed: int


class RecommendationRefreshService:
    """Queue and process durable recommendation refresh jobs."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    def __init__(
        self,
        db_session: Session,
        recommendation_engine: Optional[RecommendationEngine] = None,
        cache_service: Optional[CacheService] = None,
    ):
        self.db = db_session
        self.settings = settings
        self.cache_service = cache_service or CacheService()
        self.recommendation_engine = recommendation_engine or RecommendationEngine(db_session)
        self.precomputed_service = PrecomputedRecommendationService(db_session)
        self.data_service = DataService(db_session)

    def enqueue_seller_refresh(
        self,
        seller_id: str,
        trigger: str,
        requested_by: Optional[str] = None,
        priority: Optional[int] = None,
        details: Optional[Dict] = None,
    ) -> RefreshEnqueueResult:
        """Publish a refresh event to Kafka."""
        producer = get_kafka_producer()
        if producer:
            payload = {
                "seller_id": seller_id,
                "trigger": trigger,
                "requested_by": requested_by,
                "priority": priority or self._priority_for_trigger(trigger),
                "details": details or {}
            }
            producer.send(self.settings.kafka_refresh_topic, payload)
            producer.flush()

            if _metrics_available:
                RECOMMENDATION_REFRESH_JOBS_TOTAL.labels(trigger=trigger, status="queued").inc()

            logger.info(
                "Published recommendation refresh event to Kafka for seller %r (trigger=%s)",
                seller_id,
                trigger,
            )
            return RefreshEnqueueResult(job_id=None, created=True, status="KAFKA_PUBLISHED")

        logger.error("Failed to publish to Kafka (producer not available)")
        return RefreshEnqueueResult(job_id=None, created=False, status="KAFKA_ERROR")

    def enqueue_many_sellers(
        self,
        seller_ids: Iterable[str],
        trigger: str,
        requested_by: Optional[str] = None,
        priority: Optional[int] = None,
        details: Optional[Dict] = None,
    ) -> Dict[str, int]:
        """Publish refresh events for many sellers to Kafka."""
        unique_seller_ids = list(dict.fromkeys(seller_ids))
        if not unique_seller_ids:
            return {"queued": 0, "already_queued": 0}

        producer = get_kafka_producer()
        if producer:
            for seller_id in unique_seller_ids:
                payload = {
                    "seller_id": seller_id,
                    "trigger": trigger,
                    "requested_by": requested_by,
                    "priority": priority or self._priority_for_trigger(trigger),
                    "details": details or {}
                }
                producer.send(self.settings.kafka_refresh_topic, payload)
            producer.flush()

            queued_count = len(unique_seller_ids)

            if _metrics_available and queued_count:
                RECOMMENDATION_REFRESH_JOBS_TOTAL.labels(trigger=trigger, status="queued").inc(queued_count)

            logger.info(
                "Published %s refresh events to Kafka for trigger=%s",
                queued_count,
                trigger,
            )
            return {"queued": queued_count, "already_queued": 0}

        return {"queued": 0, "already_queued": len(unique_seller_ids)}

    def enqueue_active_sellers(
        self,
        trigger: str,
        requested_by: Optional[str] = None,
        limit: Optional[int] = None,
        priority: Optional[int] = None,
        details: Optional[Dict] = None,
    ) -> Dict[str, int]:
        """Queue refreshes for recently active sellers."""
        seller_ids = self.data_service.get_active_seller_ids(
            days=self.settings.refresh_active_sellers_lookback_days,
            limit=limit or self.settings.refresh_active_sellers_limit,
        )
        return self.enqueue_many_sellers(
            seller_ids=seller_ids,
            trigger=trigger,
            requested_by=requested_by,
            priority=priority,
            details=details,
        )

    def refresh_seller_now(
        self,
        seller_id: str,
        trigger: str,
        commit: bool = True,
        warm_cache: bool = True,
    ) -> List[Dict]:
        """
        Compute, persist, and optionally cache a full recommendation snapshot for one seller.
        """
        compute_limit = min(self.settings.precomputed_store_limit, self.settings.max_limit)
        computed_at = datetime.utcnow()
        recommendations = self.recommendation_engine.compute_recommendations(
            seller_id,
            compute_limit,
        )
        self.precomputed_service.replace_seller_recommendations(
            seller_id=seller_id,
            recommendations=recommendations,
            computed_at=computed_at,
            algorithm_version=self.settings.recommendation_algorithm_version,
        )
        if commit:
            self.db.commit()
        if warm_cache:
            self._warm_cache(seller_id, recommendations)
        return recommendations

    def run_pending_jobs(self, limit: int = 1) -> RefreshRunSummary:
        """Process pending jobs sequentially. Intended for worker or admin execution."""
        processed = succeeded = failed = 0
        for _ in range(max(limit, 0)):
            job = self._claim_next_pending_job()
            if job is None:
                break

            processed += 1
            started = time.perf_counter()
            try:
                recommendations = self.refresh_seller_now(
                    seller_id=job.sellerId,
                    trigger=job.trigger,
                    commit=False,
                    warm_cache=False,
                )
                job.status = self.COMPLETED
                job.completedAt = datetime.utcnow()
                job.lastError = None
                job.resultCount = len(recommendations)
                job.algorithmVersion = self.settings.recommendation_algorithm_version
                self.db.commit()
                self._warm_cache(job.sellerId, recommendations)
                succeeded += 1
                self._record_job_metric(job.trigger, "completed", time.perf_counter() - started)
                logger.info(
                    "Completed recommendation refresh job %s for seller %r (%s recommendations)",
                    job.id,
                    job.sellerId,
                    len(recommendations),
                )
            except Exception as exc:
                self.db.rollback()
                failed += 1
                self._retry_or_fail(job.id, exc)
                self._record_job_metric(job.trigger, "failed", time.perf_counter() - started)
                logger.error(
                    "Recommendation refresh job %s failed for seller %r: %s",
                    job.id,
                    job.sellerId,
                    exc,
                    exc_info=True,
                )

        return RefreshRunSummary(processed=processed, succeeded=succeeded, failed=failed)

    def _claim_next_pending_job(self) -> Optional[RecommendationRefreshJob]:
        query = (
            self.db.query(RecommendationRefreshJob)
            .filter(RecommendationRefreshJob.status == self.PENDING)
            .order_by(
                desc(RecommendationRefreshJob.priority),
                RecommendationRefreshJob.requestedAt.asc(),
            )
        )

        bind = self.db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        job = query.first()
        if job is None:
            return None

        job.status = self.IN_PROGRESS
        job.startedAt = datetime.utcnow()
        job.attemptCount = int(job.attemptCount or 0) + 1
        self.db.commit()
        return job

    def _retry_or_fail(self, job_id: int, error: Exception) -> None:
        """
        Re-queue the job as PENDING if attempts remain; permanently fail it otherwise.

        Priority is not reduced on retry so high-priority jobs stay high.
        """
        job = (
            self.db.query(RecommendationRefreshJob)
            .filter(RecommendationRefreshJob.id == job_id)
            .first()
        )
        if job is None:
            return

        attempts_used = int(job.attemptCount or 0)
        max_attempts = _MAX_ATTEMPTS

        if attempts_used < max_attempts:
            # Re-queue for a future worker cycle
            job.status = self.PENDING
            job.startedAt = None
            job.lastError = f"[attempt {attempts_used}/{max_attempts}] {str(error)[:500]}"
            self.db.commit()
            logger.warning(
                "Refresh job %s re-queued after failure (attempt %s/%s): %s",
                job_id,
                attempts_used,
                max_attempts,
                error,
            )
            if _metrics_available:
                RECOMMENDATION_REFRESH_JOBS_TOTAL.labels(
                    trigger=job.trigger, status="retried"
                ).inc()
        else:
            # Exhausted all attempts — mark permanently failed
            job.status = self.FAILED
            job.completedAt = datetime.utcnow()
            job.lastError = f"[final failure after {attempts_used} attempts] {str(error)[:800]}"
            self.db.commit()
            logger.error(
                "Refresh job %s permanently failed after %s attempts: %s",
                job_id,
                attempts_used,
                error,
            )

    # Keep the old name as an alias for backward compatibility with callers
    def _mark_job_failed(self, job_id: int, error: Exception) -> None:
        """Alias kept for backward compatibility; delegates to _retry_or_fail."""
        self._retry_or_fail(job_id, error)

    def _warm_cache(self, seller_id: str, recommendations: List[Dict]) -> None:
        if not self.settings.cache_enabled:
            return
        self.cache_service.set_recommendations(
            seller_id=seller_id,
            recommendations=recommendations,
            ttl=self.settings.recommendation_cache_ttl_seconds,
        )

    def _priority_for_trigger(self, trigger: str) -> int:
        if trigger in {"manual", "manual_admin", "request_miss"}:
            return self.settings.refresh_manual_priority
        if trigger in {"order_placed", "product_engaged", "product_updated", "snapshot_stale"}:
            return self.settings.refresh_event_priority
        return self.settings.refresh_schedule_priority

    def _record_job_metric(self, trigger: str, status: str, elapsed: float) -> None:
        if not _metrics_available:
            return
        RECOMMENDATION_REFRESH_JOBS_TOTAL.labels(trigger=trigger, status=status).inc()
        RECOMMENDATION_REFRESH_DURATION_SECONDS.labels(trigger=trigger, status=status).observe(elapsed)
