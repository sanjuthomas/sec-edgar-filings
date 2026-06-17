"""Tests for Kafka filing event publisher."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.messaging.filing_publisher import (
    FilingEventPublisher,
    _filing_downloaded_payload,
)
from app.models import FilingMetadata


METADATA = FilingMetadata(
    ticker="GS",
    company_name="GOLDMAN SACHS GROUP INC",
    filing_date=date(2026, 5, 15),
    form="10-Q",
    accession_number="0000886982-26-000045",
    local_path="/data/edgar/GS/000088698226000045/gs-20260515.htm",
    document_url="https://example.com/gs.htm",
    downloaded_at=datetime(2026, 6, 1, tzinfo=UTC),
)


def test_filing_downloaded_payload_includes_metadata_and_envelope():
    payload = _filing_downloaded_payload(METADATA)

    assert payload["event_type"] == "filing.downloaded"
    assert payload["schema_version"] == 1
    assert payload["ticker"] == "GS"
    assert payload["local_path"] == METADATA.local_path
    assert payload["accession_number"] == METADATA.accession_number


def test_publish_filing_downloaded_noop_when_disabled():
    publisher = FilingEventPublisher()

    async def run():
        await publisher.publish_filing_downloaded(METADATA)

    asyncio.run(run())


def test_publish_filing_downloaded_sends_message():
    publisher = FilingEventPublisher()
    mock_producer = MagicMock()
    mock_producer.send_and_wait = AsyncMock(
        return_value=MagicMock(topic="filings", partition=0, offset=42)
    )

    async def run():
        with patch.object(
            publisher,
            "_ensure_producer",
            AsyncMock(return_value=mock_producer),
        ):
            await publisher.publish_filing_downloaded(METADATA)

    asyncio.run(run())
    mock_producer.send_and_wait.assert_awaited_once()
    call_kwargs = mock_producer.send_and_wait.await_args.kwargs
    assert call_kwargs["key"] == METADATA.accession_number
    assert call_kwargs["value"]["local_path"] == METADATA.local_path


def test_publish_filing_downloaded_swallows_kafka_errors():
    publisher = FilingEventPublisher()
    mock_producer = MagicMock()
    mock_producer.send_and_wait = AsyncMock(side_effect=RuntimeError("broker down"))

    async def run():
        with patch.object(
            publisher,
            "_ensure_producer",
            AsyncMock(return_value=mock_producer),
        ):
            await publisher.publish_filing_downloaded(METADATA)

    asyncio.run(run())
