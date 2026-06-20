"""Tests for FilingStore."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.filing_store import FilingStore
from app.models import FilingMetadata


METADATA = FilingMetadata(
    ticker="GS",
    company_name="GOLDMAN SACHS GROUP INC",
    filing_date=date(2026, 5, 15),
    form="10-Q",
    accession_number="0000886982-26-000045",
    local_path="/Volumes/Transcend/edgar/GS/000088698226000045/gs-20260515.htm",
    document_url="https://example.com/gs.htm",
    downloaded_at=datetime(2026, 6, 1, tzinfo=UTC),
)


def test_filing_store_exists_and_get_by_ticker():
    store = FilingStore()
    mock_collection = MagicMock()
    mock_collection.create_index = AsyncMock()
    mock_collection.find_one = AsyncMock(return_value={"_id": METADATA.accession_number})
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": METADATA.accession_number,
                **METADATA.model_dump(mode="json"),
            }
        ]
    )
    mock_collection.find.return_value.sort.return_value = mock_cursor
    mock_collection.replace_one = AsyncMock()

    async def run():
        with patch.object(store, "_collection", return_value=mock_collection):
            assert await store.exists(METADATA.accession_number) is True
            filings = await store.get_by_ticker("GS")
            await store.put(METADATA)
        return filings

    filings = asyncio.run(run())
    assert len(filings) == 1
    assert filings[0].form == "10-Q"
    mock_collection.replace_one.assert_awaited_once()


def test_filing_store_delete_by_ticker():
    store = FilingStore()
    mock_collection = MagicMock()
    mock_collection.create_index = AsyncMock()
    mock_delete = MagicMock()
    mock_delete.deleted_count = 2
    mock_collection.delete_many = AsyncMock(return_value=mock_delete)

    async def run():
        with patch.object(store, "_collection", return_value=mock_collection):
            return await store.delete_by_ticker("gs")

    deleted = asyncio.run(run())
    assert deleted == 2
    mock_collection.delete_many.assert_awaited_once_with({"ticker": "GS"})
