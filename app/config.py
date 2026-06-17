"""Application configuration.

Values can be overridden via environment variables. The SEC requires a
descriptive ``User-Agent`` on every request, so set ``SEC_USER_AGENT`` to a
real name + contact email.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# Phrases (case-insensitive, whitespace-tolerant) that indicate a buyback /
# share repurchase authorization. These are matched against the filing text.
#
# Companies phrase buybacks inconsistently ("share" vs "stock" vs "common
# stock", "program" vs "authorization" vs "authority"), so the list is
# deliberately broad. Overlapping phrases may match the same sentence (e.g.
# "...new stock repurchase program granting authority to repurchase up to
# $25B..."); duplicate hits for a single authorization are collapsed downstream
# in the API layer, so over-matching here is safe and preferred to missing a
# real announcement.
DEFAULT_BUYBACK_TOKENS: tuple[str, ...] = (
    "repurchase program",
    "buyback program",
    "repurchase authorization",
    "repurchase authority",
    "authority to repurchase",
    "authorized the repurchase",
    "board authorized repurchase",
    "stock buyback authorization",
    "stock buyback",
    "share buyback",
    # Retained as an explicit phrase; it is a subset of "repurchase program"
    # above but kept for clarity of intent.
    "share repurchase program",
)

# SEC forms to download and index.
DEFAULT_FORMS: tuple[str, ...] = ("10-K", "10-Q", "8-K")


@dataclass(frozen=True)
class Settings:
    """Runtime settings, populated from the environment."""

    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "SEC_USER_AGENT",
            "sec-edgar-filings/0.1 (contact: set SEC_USER_AGENT env var)",
        )
    )
    lookback_days: int = field(
        default_factory=lambda: int(os.environ.get("SEC_LOOKBACK_DAYS", "365"))
    )
    # Be polite to the SEC: cap the request rate.
    max_requests_per_second: float = field(
        default_factory=lambda: float(os.environ.get("SEC_MAX_RPS", "8"))
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SEC_TIMEOUT", "30"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.environ.get("SEC_MAX_RETRIES", "3"))
    )
    # Characters of surrounding text captured around each token match.
    context_window: int = field(
        default_factory=lambda: int(os.environ.get("SEC_CONTEXT_WINDOW", "350"))
    )

    # MongoDB cache for ticker -> CIK lookups. The default points at a local
    # instance with no authentication; override MONGO_URI to supply credentials.
    mongo_uri: str = field(
        default_factory=lambda: os.environ.get(
            "MONGO_URI", "mongodb://localhost:27017"
        )
    )
    mongo_db: str = field(
        default_factory=lambda: os.environ.get("MONGO_DB", "sec_edgar_filings")
    )
    mongo_tickers_collection: str = field(
        default_factory=lambda: os.environ.get(
            "MONGO_TICKERS_COLLECTION", "tickers"
        )
    )
    mongo_filings_collection: str = field(
        default_factory=lambda: os.environ.get(
            "MONGO_FILINGS_COLLECTION", "filings"
        )
    )
    mongo_sp500_collection: str = field(
        default_factory=lambda: os.environ.get(
            "MONGO_SP500_COLLECTION", "sp500_constituents"
        )
    )
    # How long to wait for the Mongo server before giving up and falling back to
    # a direct EDGAR call (milliseconds).
    mongo_timeout_ms: int = field(
        default_factory=lambda: int(os.environ.get("MONGO_TIMEOUT_MS", "2000"))
    )
    sp500_incremental_lookback_days: int = field(
        default_factory=lambda: int(
            os.environ.get("SP500_INCREMENTAL_LOOKBACK_DAYS", "14")
        )
    )
    sp500_backfill_lookback_days: int = field(
        default_factory=lambda: int(
            os.environ.get(
                "SP500_BACKFILL_LOOKBACK_DAYS",
                os.environ.get("SEC_LOOKBACK_DAYS", "365"),
            )
        )
    )
    sp500_download_lookback_days: int = field(
        default_factory=lambda: int(
            os.environ.get("SP500_DOWNLOAD_LOOKBACK_DAYS", "30")
        )
    )
    edgar_download_base: str = field(
        default_factory=lambda: os.environ.get(
            "EDGAR_DOWNLOAD_BASE", "/Volumes/Transcend/edgar"
        )
    )
    mongo_filing_metadata_collection: str = field(
        default_factory=lambda: os.environ.get(
            "MONGO_FILING_METADATA_COLLECTION", "filing_metadata"
        )
    )
    # Minimum seconds between processing each ticker in batch jobs.
    ticker_rate_limit_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("TICKER_RATE_LIMIT_SECONDS", "60")
        )
    )

    # Kafka events emitted after a filing document is written to disk.
    kafka_enabled: bool = field(
        default_factory=lambda: os.environ.get("KAFKA_ENABLED", "false").lower()
        in ("1", "true", "yes")
    )
    kafka_bootstrap_servers: str = field(
        default_factory=lambda: os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
    )
    kafka_filing_downloaded_topic: str = field(
        default_factory=lambda: os.environ.get(
            "KAFKA_FILING_DOWNLOADED_TOPIC", "filings"
        )
    )

    forms: tuple[str, ...] = DEFAULT_FORMS
    buyback_tokens: tuple[str, ...] = DEFAULT_BUYBACK_TOKENS


settings = Settings()
