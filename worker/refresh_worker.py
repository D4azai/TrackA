"""
Database Polling Recommendation Refresh Worker.

Polls the RecommendationRefreshJob table for pending jobs and executes them.
Supports concurrent job processing via a thread pool for improved throughput.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import get_settings, setup_logging
from app.db import SessionLocal
from app.services.cache_service import CacheService
from app.services.refresh_service import RecommendationRefreshService

setup_logging()
logger = logging.getLogger("worker.refresh_worker")

_shutdown_requested: bool = False

# Number of concurrent threads for processing refresh jobs.
# Each thread gets its own DB session to avoid contention.
_WORKER_CONCURRENCY: int = 4


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — shutting down polling worker", sig_name)
    _shutdown_requested = True


def _process_single_job(job_id: int | None = None) -> dict:
    """
    Process a single pending job in its own DB session.

    Returns a summary dict: {"processed": 0|1, "succeeded": 0|1, "failed": 0|1}
    """
    db = SessionLocal()
    try:
        svc = RecommendationRefreshService(
            db_session=db,
            cache_service=CacheService(),
        )
        summary = svc.run_pending_jobs(limit=1)
        return {
            "processed": summary.processed,
            "succeeded": summary.succeeded,
            "failed": summary.failed,
        }
    except Exception as exc:
        logger.error("Worker thread error: %s", exc, exc_info=True)
        return {"processed": 0, "succeeded": 0, "failed": 1}
    finally:
        db.close()


def run_forever() -> None:
    settings = get_settings()
    poll_interval = settings.poll_interval_seconds
    batch_size = settings.worker_batch_size

    logger.info(
        "DB Polling Worker starting | env=%s poll_interval=%ss batch_size=%s concurrency=%s",
        settings.environment,
        poll_interval,
        batch_size,
        _WORKER_CONCURRENCY,
    )

    total_processed = 0
    total_succeeded = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=_WORKER_CONCURRENCY) as executor:
        while not _shutdown_requested:
            # Submit up to batch_size concurrent jobs
            futures = []
            for _ in range(batch_size):
                if _shutdown_requested:
                    break
                futures.append(executor.submit(_process_single_job))

            cycle_processed = 0
            for future in as_completed(futures):
                if _shutdown_requested:
                    break
                result = future.result()
                total_processed += result["processed"]
                total_succeeded += result["succeeded"]
                total_failed += result["failed"]
                cycle_processed += result["processed"]

            # If we processed fewer than batch size, queue is likely empty — sleep
            if cycle_processed < batch_size and not _shutdown_requested:
                time.sleep(poll_interval)

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
