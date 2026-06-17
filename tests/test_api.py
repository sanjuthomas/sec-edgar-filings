"""HTTP-level tests for the filings API."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import FilingMetadata


FILING = FilingMetadata(
    ticker="GS",
    company_name="GOLDMAN SACHS GROUP INC",
    filing_date=date(2026, 5, 15),
    form="10-Q",
    accession_number="0000886982-26-000045",
    local_path="/Volumes/Transcend/edgar/GS/000088698226000045/gs-20260515.htm",
    document_url=(
        "https://www.sec.gov/Archives/edgar/data/886982/"
        "000088698226000045/gs-20260515.htm"
    ),
    downloaded_at=datetime(2026, 6, 1, tzinfo=UTC),
)


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_filings_returns_200_with_stored_metadata():
    client = TestClient(app)
    with patch(
        "app.main.filing_store.get_by_ticker",
        AsyncMock(return_value=[FILING]),
    ):
        response = client.get("/api/filings/GS")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "GS"
    assert body["count"] == 1
    assert body["filings"][0]["form"] == "10-Q"
    assert body["filings"][0]["company_name"] == "GOLDMAN SACHS GROUP INC"


def test_get_filings_unknown_ticker_returns_404():
    client = TestClient(app)
    with patch(
        "app.main.filing_store.get_by_ticker",
        AsyncMock(return_value=[]),
    ):
        response = client.get("/api/filings/ZZZZ")

    assert response.status_code == 404
    assert "No filings found" in response.json()["detail"]
