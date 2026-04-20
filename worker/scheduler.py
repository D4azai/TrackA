"""
APScheduler-based recommendation refresh scheduler.

Runs two recurring jobs:
  1. drain_queue      — every DRAIN_INTERVAL_MINUTES (default: 5 min)
     Processes up to BATCH_SIZE pending RecommendationRefreshJobs.

  2. enqueue_active   — every ENQUEUE_INTERVAL_MINUTES (default: 30 min)
     Queues refresh jobs for all recently active sellers.

Usage:
    # Run the scheduler (blocks until SIGTERM / SIGINT)
    python -m worker.scheduler

    # Override intervals
    DRAIN_INTERVAL_MINUTES=2 ENQUEUE_INTERVAL_MINUTES=60 python -m worker.scheduler

Environment variables (all optional with defaults):
    DRAIN_INTERVAL_MINUTES      Minutes between queue-drain runs (default: 5)
    ENQUEUE_INTERVAL_MINUTES    Minutes between active-seller enqueue runs (default: 30)
    BATCH_SIZE                  Jobs per drain cycle (default: 10)
    DATABASE_URL                Postgres connection string (required)
    REDIS_URL                   Redis connection string (required)
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings, setup_logging
from app.db import SessionLocal
from app.services.cache_service import CacheService
from app.services.refresh_service import RecommendationRefreshService

setup_logging()
logger = logging.getLogger("worker.scheduler")

_DRAIN_INTERVAL_MINUTES: int = int(os.getenv("DRAIN_INTERVAL_MINUTES", "5"))
_ENQUEUE_INTERVAL_MINUTES: int = int(os.getenv("ENQUEUE_INTERVAL_MINUTES", "30"))
_BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "10"))


def _make_service() -> tuple[RecommendationRefreshService, object]:
    """Create a DB session + refresh service. Caller must close the session."""
    db = SessionLocal()
    svc = RecommendationRefreshService(
        db_session=db,
        cache_service=CacheService(),
    )
    return svc, db


# ── scheduled jobs ────────────────────────────────────────────────────────────

def drain_queue() -> None:
    """
    Process up to BATCH_SIZE pending recommendation refresh jobs.

    Called every DRAIN_INTERVAL_MINUTES by APScheduler.
    """
    svc, db = _make_service()
    try:
        summary = svc.run_pending_jobs(limit=_BATCH_SIZE)
        if summary.processed > 0:
            logger.info(
                "[drain_queue] processed=%s succeeded=%s failed=%s",
                summary.processed,
                summary.succeeded,
                summary.failed,
            )
        else:
            logger.debug("[drain_queue] queue empty")
    except Exception as exc:  # noqa: BLE001
        logger.error("[drain_queue] error: %s", exc, exc_info=True)
    finally:
        db.close()


def enqueue_active_sellers() -> None:
    """
    Queue refresh jobs for recently active sellers.

    Uses REFRESH_ACTIVE_SELLERS_LOOKBACK_DAYS and REFRESH_ACTIVE_SELLERS_LIMIT
    from settings (configurable via .env).
    Called every ENQUEUE_INTERVAL_MINUTES by APScheduler.
    """
    svc, db = _make_service()
    try:
        result = svc.enqueue_active_sellers(
            trigger="scheduled_refresh",
            requested_by="scheduler",
        )
        logger.info(
            "[enqueue_active_sellers] queued=%s already_queued=%s",
            result["queued"],
            result["already_queued"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[enqueue_active_sellers] error: %s", exc, exc_info=True)
    finally:
        db.close()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    settings = get_settings()
    logger.info(
        "Recommendation scheduler starting | env=%s drain=%smin enqueue=%smin batch=%s",
        settings.environment,
        _DRAIN_INTERVAL_MINUTES,
        _ENQUEUE_INTERVAL_MINUTES,
        _BATCH_SIZE,
    )

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        drain_queue,
        trigger=IntervalTrigger(minutes=_DRAIN_INTERVAL_MINUTES),
        id="drain_queue",
        name="Drain recommendation refresh job queue",
        max_instances=1,          # Never run two drains in parallel
        coalesce=True,            # Skip missed runs rather than pile up
        replace_existing=True,
    )

    scheduler.add_job(
        enqueue_active_sellers,
        trigger=IntervalTrigger(minutes=_ENQUEUE_INTERVAL_MINUTES),
        id="enqueue_active_sellers",
        name="Enqueue active seller refreshes",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Also run both jobs immediately on startup so the first cycle doesn't
    # wait for the full interval.
    scheduler.add_job(enqueue_active_sellers, id="enqueue_active_sellers_boot", name="Boot: enqueue active sellers")
    scheduler.add_job(drain_queue, id="drain_queue_boot", name="Boot: drain queue")

    def _handle_signal(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down scheduler", sig_name)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
