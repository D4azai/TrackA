"""
APScheduler-based recommendation refresh scheduler.

Runs a recurring job:
  1. enqueue_active   — every ENQUEUE_INTERVAL_MINUTES (default: 30 min)
     Queues refresh jobs for all recently active sellers.

Usage:
    # Run the scheduler (blocks until SIGTERM / SIGINT)
    python -m worker.scheduler

    # Override intervals
    ENQUEUE_INTERVAL_MINUTES=60 python -m worker.scheduler

Environment variables (all optional with defaults):
    ENQUEUE_INTERVAL_MINUTES    Minutes between active-seller enqueue runs (default: 30)
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

_ENQUEUE_INTERVAL_MINUTES: int = int(os.getenv("ENQUEUE_INTERVAL_MINUTES", "30"))


def _make_service() -> tuple[RecommendationRefreshService, object]:
    """Create a DB session + refresh service. Caller must close the session."""
    db = SessionLocal()
    svc = RecommendationRefreshService(
        db_session=db,
        cache_service=CacheService(),
    )
    return svc, db


# ── scheduled jobs ────────────────────────────────────────────────────────────

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
        "Recommendation scheduler starting | env=%s enqueue=%smin",
        settings.environment,
        _ENQUEUE_INTERVAL_MINUTES,
    )

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        enqueue_active_sellers,
        trigger=IntervalTrigger(minutes=_ENQUEUE_INTERVAL_MINUTES),
        id="enqueue_active_sellers",
        name="Enqueue active seller refreshes",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    def _boot_enqueue_active_sellers():
        from app.services.refresh_service import get_kafka_producer
        import time
        for _ in range(15):
            if get_kafka_producer() is not None:
                break
            logger.info("Waiting for Kafka before boot enqueue...")
            time.sleep(2)
        enqueue_active_sellers()

    # Run job immediately on startup so the first cycle doesn't wait for full interval
    scheduler.add_job(_boot_enqueue_active_sellers, id="enqueue_active_sellers_boot", name="Boot: enqueue active sellers")

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
