"""Tests for the read-only browse API."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import FilingMetadata, FileSystemEntry, FilesystemBrowseResult

FILING = FilingMetadata(
    ticker="GS",
    company_name="GOLDMAN SACHS GROUP INC",
    filing_date=date(2026, 5, 15),
    form="10-Q",
    accession_number="0000886982-26-000045",
    local_path="/tmp/edgar/GS/000088698226000045/gs-20260515.htm",
    document_url=(
        "https://www.sec.gov/Archives/edgar/data/886982/"
        "000088698226000045/gs-20260515.htm"
    ),
    downloaded_at=datetime(2026, 6, 1, tzinfo=UTC),
)

FILESYSTEM = FilesystemBrowseResult(
    base_path="/tmp/edgar",
    ticker_path="/tmp/edgar/GS",
    exists=True,
    accession_count=1,
    file_count=1,
    entries=[
        FileSystemEntry(
            relative_path="000088698226000045/gs-20260515.htm",
            accession_dir="000088698226000045",
            name="gs-20260515.htm",
            size_bytes=12345,
            modified_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    ],
)


def test_browse_ticker_returns_mongo_and_filesystem():
    client = TestClient(app)
    with (
        patch(
            "app.api.browse.filing_store.get_by_ticker",
            AsyncMock(return_value=[FILING]),
        ),
        patch(
            "app.api.browse.list_ticker_filesystem",
            return_value=FILESYSTEM,
        ),
    ):
        response = client.get("/api/browse/GS")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "GS"
    assert body["company_name"] == "GOLDMAN SACHS GROUP INC"
    assert body["mongo"]["count"] == 1
    assert body["mongo"]["filings"][0]["form"] == "10-Q"
    assert body["filesystem"]["file_count"] == 1
    assert body["filesystem"]["entries"][0]["name"] == "gs-20260515.htm"


def test_browse_ticker_empty_data_returns_200():
    client = TestClient(app)
    empty_fs = FilesystemBrowseResult(
        base_path="/tmp/edgar",
        ticker_path="/tmp/edgar/ZZZZ",
        exists=False,
        accession_count=0,
        file_count=0,
        entries=[],
    )
    with (
        patch(
            "app.api.browse.filing_store.get_by_ticker",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.api.browse.list_ticker_filesystem",
            return_value=empty_fs,
        ),
    ):
        response = client.get("/api/browse/ZZZZ")

    assert response.status_code == 200
    body = response.json()
    assert body["mongo"]["count"] == 0
    assert body["filesystem"]["exists"] is False


def test_browse_invalid_ticker_returns_400():
    client = TestClient(app)
    response = client.get("/api/browse/not%20valid")
    assert response.status_code == 400


def test_browse_page_served():
    client = TestClient(app)
    response = client.get("/browse")
    assert response.status_code == 200
    assert "Read-only ticker browser" in response.text


def test_list_ticker_filesystem_reads_disk(tmp_path: Path):
    from app.browse.filesystem import list_ticker_filesystem

    ticker_dir = tmp_path / "GS" / "000088698226000045"
    ticker_dir.mkdir(parents=True)
    file_path = ticker_dir / "gs-20260515.htm"
    file_path.write_text("<html></html>", encoding="utf-8")

    with patch("app.browse.filesystem.settings") as mock_settings:
        mock_settings.edgar_download_base = str(tmp_path)
        result = list_ticker_filesystem("gs")

    assert result.exists is True
    assert result.accession_count == 1
    assert result.file_count == 1
    assert result.entries[0].name == "gs-20260515.htm"
    assert result.entries[0].size_bytes == file_path.stat().st_size
