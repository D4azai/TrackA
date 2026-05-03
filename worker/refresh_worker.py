"""
Database Polling Recommendation Refresh Worker.

Polls the RecommendationRefreshJob table for pending jobs and executes them.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
import os

from app.config import get_settings, setup_logging
from app.db import SessionLocal
from app.services.cache_service import CacheService
from app.services.refresh_service import RecommendationRefreshService

setup_logging()
logger = logging.getLogger("worker.refresh_worker")

_shutdown_requested: bool = False

def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — shutting down polling worker", sig_name)
    _shutdown_requested = True

def _build_refresh_service(db_session) -> RecommendationRefreshService:
    return RecommendationRefreshService(
        db_session=db_session,
        cache_service=CacheService(),
    )

def run_forever() -> None:
    settings = get_settings()
    poll_interval = settings.poll_interval_seconds
    batch_size = settings.worker_batch_size

    logger.info(
        "DB Polling Worker starting | env=%s poll_interval=%ss batch_size=%s",
        settings.environment,
        poll_interval,
        batch_size,
    )

    total_processed = 0
    total_succeeded = 0
    total_failed = 0

    while not _shutdown_requested:
        db = SessionLocal()
        try:
            svc = _build_refresh_service(db)
            summary = svc.run_pending_jobs(limit=batch_size)
            
            total_processed += summary.processed
            total_succeeded += summary.succeeded
            total_failed += summary.failed
            
            # If we processed fewer than batch size, queue is empty, so we sleep.
            # If we processed full batch size, immediately loop to get more.
            if summary.processed < batch_size and not _shutdown_requested:
                time.sleep(poll_interval)
                
        except Exception as exc:
            logger.error("Worker error during polling: %s", exc, exc_info=True)
            time.sleep(poll_interval)
        finally:
            db.close()

    logger.info(
        "DB Polling worker stopped | total: processed=%s succeeded=%s failed=%s",
        total_processed,
        total_succeeded,
        total_failed,
    )

def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run_forever()
    return 0

if __name__ == "__main__":
    sys.exit(main())
