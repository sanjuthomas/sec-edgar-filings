"""Tests for S&P 500 universe fetch/parse and Mongo refresh."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.universe_store import UniverseStore
from app.models import Sp500Constituent
from app.universe.sp500 import parse_sp500_constituents

SAMPLE_HTML = """
<html><body>
<table class="wikitable sortable">
  <tr>
    <th>Symbol</th><th>Security</th><th>GICS Sector</th>
  </tr>
  <tr><td>ADBE</td><td>Adobe Inc.</td><td>Information Technology</td></tr>
  <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
</table>
<table class="wikitable sortable">
  <tr>
    <th>Effective Date</th><th>Added</th><th>Removed</th>
  </tr>
  <tr><td>2026-01-01</td><td>XYZ</td><td>OLD</td></tr>
</table>
</body></html>
"""


def test_parse_sp500_constituents_reads_primary_table():
    constituents = parse_sp500_constituents(SAMPLE_HTML)
    assert [c.model_dump() for c in constituents] == [
        {"ticker": "ADBE", "company_name": "Adobe Inc."},
        {"ticker": "BRK.B", "company_name": "Berkshire Hathaway"},
    ]


def test_parse_sp500_constituents_raises_when_table_missing():
    with pytest.raises(ValueError, match="not found"):
        parse_sp500_constituents("<html><body></body></html>")


def test_wikipedia_user_agent_requires_contact_when_sec_default(monkeypatch):
    monkeypatch.delenv("WIKIPEDIA_USER_AGENT", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    from app.universe import sp500

    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        sp500.wikipedia_user_agent()


def test_wikipedia_user_agent_uses_sec_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "My App (contact: me@example.com)")
    from app.universe import sp500

    assert sp500.wikipedia_user_agent() == "My App (contact: me@example.com)"


def test_refresh_sp500_marks_additions_and_removals():
    async def run() -> None:
        store = UniverseStore()
        mock_collection = MagicMock()
        mock_collection.find.return_value.sort.return_value.to_list = AsyncMock(
            return_value=[{"_id": "OLD"}, {"_id": "ADBE"}]
        )
        mock_collection.bulk_write = AsyncMock()

        fixed_now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
        constituents = [
            Sp500Constituent(ticker="ADBE", company_name="Adobe Inc."),
            Sp500Constituent(ticker="MSFT", company_name="Microsoft"),
        ]

        with (
            patch.object(store, "_collection", return_value=mock_collection),
            patch("app.db.universe_store.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = fixed_now
            result = await store.refresh_sp500(constituents)

        assert result.active_count == 2
        assert result.added == ["MSFT"]
        assert result.removed == ["OLD"]
        assert result.refreshed_at == fixed_now
        mock_collection.bulk_write.assert_awaited_once()

    asyncio.run(run())


def test_get_active_sp500_tickers_returns_sorted_symbols():
    async def run() -> None:
        store = UniverseStore()
        mock_collection = MagicMock()
        mock_collection.find.return_value.sort.return_value.to_list = AsyncMock(
            return_value=[{"_id": "MSFT"}, {"_id": "ADBE"}]
        )

        with patch.object(store, "_collection", return_value=mock_collection):
            tickers = await store.get_active_sp500_tickers()

        assert tickers == ["MSFT", "ADBE"]

    asyncio.run(run())
