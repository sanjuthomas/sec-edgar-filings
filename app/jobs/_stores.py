"""Shared helpers for batch job CLIs."""

from __future__ import annotations

from app.db.document_store import document_store
from app.db.filing_store import filing_store
from app.db.ticker_store import ticker_store
from app.db.universe_store import universe_store
from app.messaging.filing_publisher import filing_event_publisher


def close_job_stores() -> None:
    """Release Mongo connection pools opened during a batch job."""

    universe_store.close()
    ticker_store.close()
    document_store.close()
    filing_store.close()
    filing_event_publisher.close()
