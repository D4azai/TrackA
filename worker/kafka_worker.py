"""
Kafka Recommendation Refresh Worker.

Consumes events from the Kafka refresh topic and runs recommendation refreshes.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from typing import Optional

from kafka import KafkaConsumer

from app.config import get_settings, setup_logging
from app.db import SessionLocal
from app.services.cache_service import CacheService
from app.services.refresh_service import RecommendationRefreshService

setup_logging()
logger = logging.getLogger("worker.kafka_worker")

_shutdown_requested: bool = False

def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — shutting down Kafka worker", sig_name)
    _shutdown_requested = True

def _build_refresh_service(db_session) -> RecommendationRefreshService:
    return RecommendationRefreshService(
        db_session=db_session,
        cache_service=CacheService(),
    )

def run_forever() -> None:
    settings = get_settings()
    logger.info(
        "Kafka Refresh Worker starting | env=%s topic=%s brokers=%s",
        settings.environment,
        settings.kafka_refresh_topic,
        settings.kafka_bootstrap_servers,
    )

    try:
        consumer = KafkaConsumer(
            settings.kafka_refresh_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id='recommendation-refresh-group',
            auto_offset_reset='earliest',
            enable_auto_commit=True,
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
    except Exception as exc:
        logger.error("Failed to initialize Kafka consumer: %s", exc)
        sys.exit(1)

    total_processed = 0
    total_succeeded = 0
    total_failed = 0

    while not _shutdown_requested:
        # Poll Kafka for messages (timeout 1000ms)
        msg_pack = consumer.poll(timeout_ms=1000)
        
        for tp, messages in msg_pack.items():
            for msg in messages:
                if _shutdown_requested:
                    break
                    
                payload = msg.value
                seller_id = payload.get("seller_id")
                trigger = payload.get("trigger", "kafka_event")
                
                if not seller_id:
                    logger.warning("Received event with no seller_id: %s", payload)
                    continue
                    
                total_processed += 1
                t_start = time.perf_counter()
                
                db = SessionLocal()
                try:
                    svc = _build_refresh_service(db)
                    logger.info("Processing Kafka event for seller %r (trigger=%s)", seller_id, trigger)
                    svc.refresh_seller_now(
                        seller_id=seller_id,
                        trigger=trigger,
                        commit=True,
                        warm_cache=True,
                    )
                    total_succeeded += 1
                    logger.info("Successfully refreshed seller %r in %.0fms", seller_id, (time.perf_counter() - t_start) * 1000)
                except Exception as exc:
                    total_failed += 1
                    logger.error("Failed to refresh seller %r from Kafka event: %s", seller_id, exc, exc_info=True)
                finally:
                    db.close()

    logger.info(
        "Kafka Refresh worker stopped | "
        "total: processed=%s succeeded=%s failed=%s",
        total_processed,
        total_succeeded,
        total_failed,
    )
    consumer.close()

def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run_forever()
    return 0

if __name__ == "__main__":
    sys.exit(main())
