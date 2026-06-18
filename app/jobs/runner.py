"""High-level download orchestration used by the API job manager."""

from __future__ import annotations

import logging

from app.db.filing_store import filing_store
from app.db.universe_store import universe_store
from app.download.service import download_ticker
from app.edgar.client import EdgarClient, EdgarError
from app.jobs.download_sp500 import run_download_sp500
from app.models import DownloadTickerResult, Sp500DownloadResult

logger = logging.getLogger(__name__)


async def run_single_ticker_download(
    ticker: str,
    *,
    lookback_days: int,
) -> DownloadTickerResult:
    """Download filings for one ticker within the lookback window."""

    normalized = ticker.strip().upper()
    async with EdgarClient() as client:
        try:
            result = await download_ticker(
                client, normalized, lookback_days=lookback_days
            )
        except EdgarError as exc:
            await universe_store.record_download(
                normalized,
                status="error",
                lookback_days=lookback_days,
                error=str(exc),
            )
            raise

    await universe_store.record_download(
        normalized,
        status="ok",
        lookback_days=result.lookback_days,
        filings_found=result.filings_found,
        filings_downloaded=result.filings_downloaded,
        filings_skipped=result.filings_skipped,
    )
    return result


async def run_full_reload(
    *,
    lookback_days: int,
    skip_refresh: bool = False,
    on_ticker_start=None,
    on_ticker_complete=None,
) -> tuple[int, int, Sp500DownloadResult]:
    """Clear filing metadata and re-download the S&P 500 universe."""

    deleted_metadata = await filing_store.clear_all()
    reset_tickers = await universe_store.reset_download_status()
    logger.info(
        "Full reload prep: cleared %d filing metadata docs, reset %d tickers",
        deleted_metadata,
        reset_tickers,
    )

    summary = await run_download_sp500(
        mode="backfill",
        skip_refresh=skip_refresh,
        lookback_days=lookback_days,
        initialize=False,
        on_ticker_start=on_ticker_start,
        on_ticker_complete=on_ticker_complete,
    )
    return deleted_metadata, reset_tickers, summary
