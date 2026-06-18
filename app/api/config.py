"""Runtime configuration API."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.db.filing_store import filing_store
from app.models import RuntimeConfig

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=RuntimeConfig)
async def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        kafka_enabled=settings.kafka_enabled,
        kafka_bootstrap_servers=settings.kafka_bootstrap_servers,
        kafka_filing_downloaded_topic=settings.kafka_filing_downloaded_topic,
        edgar_download_base=settings.edgar_download_base,
        ticker_rate_limit_seconds=settings.ticker_rate_limit_seconds,
        default_lookback_days=settings.lookback_days,
        sp500_download_lookback_days=settings.sp500_download_lookback_days,
    )


@router.get("/stats")
async def get_stats() -> dict[str, int | bool]:
    return {
        "filing_metadata_count": await filing_store.count_all(),
        "kafka_enabled": settings.kafka_enabled,
    }
