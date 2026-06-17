"""Tests for S&P 500 download batch job."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.jobs._lookback import effective_sp500_lookback_days
from app.jobs.download_sp500 import run_download_sp500
from app.models import DownloadTickerResult, UniverseRefreshResult


def test_effective_sp500_lookback_uses_backfill_for_first_scan():
    with patch("app.jobs._lookback.settings") as mock_settings:
        mock_settings.sp500_incremental_lookback_days = 14
        mock_settings.sp500_backfill_lookback_days = 365
        assert (
            effective_sp500_lookback_days(mode="incremental", last_scan_at=None)
            == 365
        )


def test_effective_sp500_lookback_widens_after_gap():
    with patch("app.jobs._lookback.settings") as mock_settings:
        mock_settings.sp500_incremental_lookback_days = 14
        mock_settings.sp500_backfill_lookback_days = 365
        last_scan = datetime(2026, 5, 1, tzinfo=UTC)
        now = datetime(2026, 6, 6, tzinfo=UTC)
        assert (
            effective_sp500_lookback_days(
                mode="incremental",
                last_scan_at=last_scan,
                now=now,
            )
            == 37
        )


def test_run_download_sp500_processes_active_tickers():
    refresh_result = UniverseRefreshResult(
        source="wikipedia",
        source_url="https://example.com",
        refreshed_at=datetime(2026, 6, 6, tzinfo=UTC),
        active_count=2,
        added=["MSFT"],
        removed=[],
    )
    download_result = DownloadTickerResult(
        ticker="ADBE",
        cik="0000796343",
        company_name="ADOBE INC.",
        lookback_days=30,
        filings_found=2,
        filings_downloaded=1,
        filings_skipped=1,
    )

    async def run():
        mock_download = AsyncMock(return_value=download_result)
        with (
            patch(
                "app.jobs.download_sp500.fetch_sp500_constituents",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.jobs.download_sp500.universe_store.refresh_sp500",
                AsyncMock(return_value=refresh_result),
            ),
            patch(
                "app.jobs.download_sp500.universe_store.get_active_sp500_tickers",
                AsyncMock(return_value=["ADBE", "MSFT"]),
            ),
            patch(
                "app.jobs.download_sp500.universe_store.get_last_download_at",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.jobs.download_sp500.download_ticker",
                mock_download,
            ),
            patch(
                "app.jobs.download_sp500.universe_store.record_download",
                AsyncMock(),
            ) as mock_record,
            patch("app.jobs.download_sp500.EdgarClient") as mock_client_cls,
            patch(
                "app.jobs.download_sp500.asyncio.sleep",
                AsyncMock(),
            ) as mock_sleep,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            summary = await run_download_sp500(mode="backfill")

        assert summary.tickers_total == 2
        assert summary.tickers_processed == 2
        assert summary.tickers_failed == 0
        assert summary.total_filings_downloaded == 2
        assert mock_download.await_count == 2
        assert mock_record.await_count == 2
        assert mock_sleep.await_count == 1
        return summary

    asyncio.run(run())
