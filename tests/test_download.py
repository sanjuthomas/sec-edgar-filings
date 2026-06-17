"""Tests for filing download service."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.download.service import download_filing, download_ticker
from app.messaging.filing_publisher import FilingEventPublisher
from app.models import Filing


FILING = Filing(
    form="10-Q",
    filing_date=date(2026, 5, 15),
    report_date=date(2026, 3, 31),
    accession_number="0000886982-26-000045",
    primary_document="gs-20260515.htm",
    document_url=(
        "https://www.sec.gov/Archives/edgar/data/886982/"
        "000088698226000045/gs-20260515.htm"
    ),
)


def test_download_filing_skips_when_accession_exists(tmp_path: Path):
    mock_store = AsyncMock()
    mock_store.exists.return_value = True
    mock_client = AsyncMock()

    async def run():
        return await download_filing(
            mock_client,
            FILING,
            ticker="GS",
            company_name="GOLDMAN SACHS GROUP INC",
            store=mock_store,
            base_dir=tmp_path,
        )

    result = asyncio.run(run())
    assert result is None
    mock_client.get_text.assert_not_called()


def test_download_filing_writes_file_and_metadata(tmp_path: Path):
    mock_store = AsyncMock()
    mock_store.exists.return_value = False
    mock_publisher = AsyncMock(spec=FilingEventPublisher)
    mock_client = AsyncMock()
    mock_client.get_text.return_value = "<html>filing</html>"

    async def run():
        return await download_filing(
            mock_client,
            FILING,
            ticker="GS",
            company_name="GOLDMAN SACHS GROUP INC",
            store=mock_store,
            publisher=mock_publisher,
            base_dir=tmp_path,
        )

    result = asyncio.run(run())
    assert result is not None
    assert result.ticker == "GS"
    assert result.form == "10-Q"
    assert Path(result.local_path).exists()
    mock_publisher.publish_filing_downloaded.assert_awaited_once_with(result)
    mock_store.put.assert_awaited_once()


def test_download_filing_publishes_kafka_when_file_already_on_disk(tmp_path: Path):
    mock_store = AsyncMock()
    mock_store.exists.return_value = False
    mock_publisher = AsyncMock(spec=FilingEventPublisher)
    mock_client = AsyncMock()

    dest_dir = tmp_path / "GS" / "000088698226000045"
    dest_dir.mkdir(parents=True)
    existing = dest_dir / FILING.primary_document
    existing.write_text("<html>cached</html>", encoding="utf-8")

    async def run():
        return await download_filing(
            mock_client,
            FILING,
            ticker="GS",
            company_name="GOLDMAN SACHS GROUP INC",
            store=mock_store,
            publisher=mock_publisher,
            base_dir=tmp_path,
        )

    result = asyncio.run(run())
    assert result is not None
    mock_client.get_text.assert_not_called()
    mock_publisher.publish_filing_downloaded.assert_awaited_once_with(result)
    mock_store.put.assert_awaited_once()


def test_download_ticker_counts_downloaded_and_skipped(tmp_path: Path):
    mock_store = AsyncMock()
    mock_store.exists.side_effect = [False, True]
    mock_client = AsyncMock()
    mock_client.get_text.return_value = "<html>filing</html>"
    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = ("0000886982", "GOLDMAN SACHS GROUP INC")

    async def run():
        with (
            patch(
                "app.download.service.TickerResolver",
                return_value=mock_resolver,
            ),
            patch(
                "app.download.service.fetch_recent_filings",
                AsyncMock(return_value=[FILING, FILING]),
            ),
        ):
            return await download_ticker(
                mock_client,
                "GS",
                lookback_days=30,
                store=mock_store,
                base_dir=tmp_path,
            )

    result = asyncio.run(run())
    assert result.filings_found == 2
    assert result.filings_downloaded == 1
    assert result.filings_skipped == 1
