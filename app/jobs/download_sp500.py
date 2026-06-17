"""Batch job to download S&P 500 filings to local storage.

Usage::

    python -m app.jobs.download_sp500
    python -m app.jobs.download_sp500 --skip-refresh --resume-from MSFT
    python -m app.jobs.download_sp500 --lookback-days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.config import settings
from app.db.universe_store import universe_store
from app.download.service import download_ticker
from app.edgar.client import EdgarClient, EdgarError
from app.jobs._lookback import effective_sp500_lookback_days
from app.jobs._stores import close_job_stores
from app.models import Sp500DownloadResult, UniverseRefreshResult
from app.startup import initialize_runtime
from app.universe.sp500 import fetch_sp500_constituents

logger = logging.getLogger(__name__)


async def run_download_sp500(
    *,
    mode: str = "incremental",
    skip_refresh: bool = False,
    resume_from: str | None = None,
    lookback_days: int | None = None,
) -> Sp500DownloadResult:
    """Refresh the S&P 500 list and download filings for each active ticker."""

    await initialize_runtime()

    refresh_result: UniverseRefreshResult | None = None
    if skip_refresh:
        tickers = await universe_store.get_active_sp500_tickers()
        if not tickers:
            raise RuntimeError(
                "No active S&P 500 tickers in Mongo. Run without --skip-refresh "
                "or execute python -m app.jobs.refresh_sp500 first."
            )
    else:
        constituents = await fetch_sp500_constituents()
        refresh_result = await universe_store.refresh_sp500(constituents)
        tickers = await universe_store.get_active_sp500_tickers()

    if resume_from:
        resume_from = resume_from.upper()
        tickers = [ticker for ticker in tickers if ticker >= resume_from]

    failed_tickers: list[str] = []
    total_downloaded = 0
    total_skipped = 0
    tickers_processed = 0

    async with EdgarClient() as client:
        for i, ticker in enumerate(tickers):
            if i > 0 and settings.ticker_rate_limit_seconds > 0:
                await asyncio.sleep(settings.ticker_rate_limit_seconds)

            last_download_at = await universe_store.get_last_download_at(ticker)
            effective_lookback = effective_sp500_lookback_days(
                mode=mode,
                last_scan_at=last_download_at,
                lookback_days=lookback_days,
            )
            if mode == "backfill" and lookback_days is None:
                effective_lookback = settings.sp500_download_lookback_days
            elif lookback_days is None and last_download_at is None:
                effective_lookback = settings.sp500_download_lookback_days

            try:
                result = await download_ticker(
                    client, ticker, lookback_days=effective_lookback
                )
            except EdgarError as exc:
                failed_tickers.append(ticker)
                logger.error("Download failed for %s: %s", ticker, exc)
                await universe_store.record_download(
                    ticker,
                    status="error",
                    lookback_days=effective_lookback,
                    error=str(exc),
                )
                continue

            tickers_processed += 1
            total_downloaded += result.filings_downloaded
            total_skipped += result.filings_skipped
            await universe_store.record_download(
                ticker,
                status="ok",
                lookback_days=result.lookback_days,
                filings_found=result.filings_found,
                filings_downloaded=result.filings_downloaded,
                filings_skipped=result.filings_skipped,
            )
            logger.info(
                "Downloaded %s: lookback=%d found=%d new=%d skipped=%d",
                ticker,
                result.lookback_days,
                result.filings_found,
                result.filings_downloaded,
                result.filings_skipped,
            )

    summary = Sp500DownloadResult(
        mode=mode,
        universe_refresh=refresh_result,
        tickers_total=len(tickers),
        tickers_processed=tickers_processed,
        tickers_failed=len(failed_tickers),
        failed_tickers=failed_tickers,
        total_filings_downloaded=total_downloaded,
        total_filings_skipped=total_skipped,
    )
    if refresh_result is not None:
        logger.info(
            "Universe refresh: active=%d added=%d removed=%d",
            refresh_result.active_count,
            len(refresh_result.added),
            len(refresh_result.removed),
        )
    logger.info(
        "Download batch complete: processed=%d failed=%d downloaded=%d skipped=%d",
        summary.tickers_processed,
        summary.tickers_failed,
        summary.total_filings_downloaded,
        summary.total_filings_skipped,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh the S&P 500 universe and download recent filings for "
            "each active ticker."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("incremental", "backfill"),
        default="backfill",
        help=(
            "backfill uses SP500_DOWNLOAD_LOOKBACK_DAYS (default 30) for "
            "every ticker; incremental widens the window since the last run."
        ),
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Skip the Wikipedia universe refresh and use existing Mongo tickers.",
    )
    parser.add_argument(
        "--resume-from",
        metavar="TICKER",
        help="Process only tickers at or after this symbol (upper-case sort order).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        metavar="N",
        help="Override the computed lookback window for every ticker.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        summary = asyncio.run(
            run_download_sp500(
                mode=args.mode,
                skip_refresh=args.skip_refresh,
                resume_from=args.resume_from,
                lookback_days=args.lookback_days,
            )
        )
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        close_job_stores()

    print(summary.model_dump_json(indent=2))
    return 1 if summary.tickers_failed else 0


if __name__ == "__main__":
    sys.exit(main())
