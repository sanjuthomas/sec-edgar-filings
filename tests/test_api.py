"""HTTP-level tests for the buybacks API."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.edgar.client import EdgarError
from app.main import app
from app.models import BuybackAnnouncement, Filing

FILING = Filing(
    form="8-K",
    filing_date=date(2026, 4, 21),
    report_date=date(2026, 4, 15),
    accession_number="0000796343-26-000101",
    primary_document="adbe-20260415.htm",
    document_url=(
        "https://www.sec.gov/Archives/edgar/data/796343/"
        "000079634326000101/adbe-20260415.htm"
    ),
)

ANNOUNCEMENT = BuybackAnnouncement(
    event_type="new_authorization",
    announcement_date=date(2026, 4, 21),
    authorization_date=date(2026, 4, 21),
    report_date=date(2026, 4, 15),
    authorization_amount=25_000_000_000.0,
    authorization_amount_text="$25 billion",
    amount_context="approved a new stock repurchase program",
    matched_token="repurchase program",
    form="8-K",
    filing_date=date(2026, 4, 21),
    filing_url=FILING.document_url,
)


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_buybacks_returns_200_with_mocked_edgar():
    client = TestClient(app)
    mock_client = AsyncMock()
    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = ("0000796343", "ADOBE INC.")

    with (
        patch("app.main.EdgarClient", return_value=mock_client),
        patch("app.main.TickerResolver", return_value=mock_resolver),
        patch(
            "app.main.fetch_recent_filings",
            AsyncMock(return_value=[FILING]),
        ),
        patch(
            "app.main._scan_filing",
            AsyncMock(return_value=[ANNOUNCEMENT]),
        ),
    ):
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        response = client.get("/api/buybacks/ADBE")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "ADBE"
    assert body["cik"] == "0000796343"
    assert body["count"] == 1
    assert body["announcements"][0]["event_type"] == "new_authorization"


def test_get_buybacks_unknown_ticker_returns_404():
    client = TestClient(app)
    mock_client = AsyncMock()
    mock_resolver = AsyncMock()
    mock_resolver.resolve.side_effect = EdgarError("Unknown ticker: 'ZZZZ'")

    with (
        patch("app.main.EdgarClient", return_value=mock_client),
        patch("app.main.TickerResolver", return_value=mock_resolver),
    ):
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        response = client.get("/api/buybacks/ZZZZ")

    assert response.status_code == 404
    assert "Unknown ticker" in response.json()["detail"]
