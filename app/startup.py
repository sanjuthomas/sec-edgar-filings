"""Startup logging and runtime initialization."""

from __future__ import annotations

import logging

from app.config import settings
from app.edgar.filings import ARCHIVES_URL, HISTORY_URL, SUBMISSIONS_URL
from app.edgar.tickers import COMPANY_TICKERS_URL
from app.messaging.filing_publisher import filing_event_publisher
from app.universe.sp500 import SP500_WIKIPEDIA_URL

logger = logging.getLogger(__name__)


def log_startup_config() -> None:
    """Log external services and storage configured for this process."""

    logger.info("=== SEC EDGAR Filings startup ===")
    logger.info(
        "SEC EDGAR: submissions=%s history=%s archives=%s tickers=%s",
        SUBMISSIONS_URL,
        HISTORY_URL,
        ARCHIVES_URL,
        COMPANY_TICKERS_URL,
    )
    logger.info(
        "SEC client: max_rps=%.1f timeout=%ss max_retries=%d user_agent=%r",
        settings.max_requests_per_second,
        settings.request_timeout_seconds,
        settings.max_retries,
        settings.user_agent,
    )
    logger.info(
        "MongoDB: uri=%s db=%s timeout_ms=%d",
        settings.mongo_uri,
        settings.mongo_db,
        settings.mongo_timeout_ms,
    )
    logger.info(
        "MongoDB collections: tickers=%s filings=%s filing_metadata=%s sp500=%s",
        settings.mongo_tickers_collection,
        settings.mongo_filings_collection,
        settings.mongo_filing_metadata_collection,
        settings.mongo_sp500_collection,
    )
    logger.info("Local filing storage: base_path=%s", settings.edgar_download_base)
    logger.info("S&P 500 universe source: %s", SP500_WIKIPEDIA_URL)
    if settings.kafka_enabled:
        logger.info(
            "Kafka: enabled bootstrap_servers=%s topic=%s",
            settings.kafka_bootstrap_servers,
            settings.kafka_filing_downloaded_topic,
        )
    else:
        logger.info(
            "Kafka: disabled (set KAFKA_ENABLED=true to publish filing events)"
        )


async def initialize_runtime() -> None:
    """Log configuration and connect optional runtime services."""

    log_startup_config()
    if settings.kafka_enabled:
        await filing_event_publisher.connect()
