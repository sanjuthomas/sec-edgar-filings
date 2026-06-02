"""Resolve a stock ticker to its SEC CIK.

Lookups are served from a MongoDB cache first. On a cache miss the SEC's
published ticker->CIK map is fetched from EDGAR, stored in MongoDB for next
time, and used to satisfy the request. If Mongo is unavailable the resolver
falls back to the EDGAR map transparently.
"""

from __future__ import annotations

import asyncio

from app.db.ticker_store import TickerStore, ticker_store
from app.edgar.client import EdgarClient, EdgarError

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _candidate_keys(ticker: str) -> list[str]:
    """Lookup keys to try for ``ticker``.

    Class-share tickers are commonly written with a dot (``BRK.B``), but the
    SEC's map uses a dash (``BRK-B``); try the input as given, then with ``.``
    normalized to ``-``.
    """

    base = ticker.strip().upper()
    keys = [base]
    dashed = base.replace(".", "-")
    if dashed != base:
        keys.append(dashed)
    return keys


class TickerResolver:
    """Resolves tickers to CIK + company name via a Mongo cache + EDGAR."""

    def __init__(
        self, client: EdgarClient, store: TickerStore | None = None
    ) -> None:
        self._client = client
        self._store = store or ticker_store
        self._map: dict[str, tuple[str, str]] | None = None
        self._lock = asyncio.Lock()

    async def _load_from_edgar(self) -> dict[str, tuple[str, str]]:
        """Fetch and parse the full SEC ticker map (cached in-process)."""

        if self._map is not None:
            return self._map
        async with self._lock:
            if self._map is not None:
                return self._map
            raw = await self._client.get_json(COMPANY_TICKERS_URL)
            mapping: dict[str, tuple[str, str]] = {}
            # The payload is a dict keyed by row index; each value has
            # cik_str, ticker, and title.
            for entry in raw.values():
                ticker = str(entry["ticker"]).upper()
                cik = f"{int(entry['cik_str']):010d}"
                title = str(entry.get("title", ""))
                mapping[ticker] = (cik, title)
            self._map = mapping
            return self._map

    async def resolve(self, ticker: str) -> tuple[str, str]:
        """Return ``(cik, company_name)`` for ``ticker``.

        Tries the MongoDB cache first; on a miss, fetches the SEC map from
        EDGAR, persists it to Mongo, and resolves from the freshly loaded map.

        Raises ``EdgarError`` (with a clear message) if the ticker is unknown.
        """

        keys = _candidate_keys(ticker)

        for key in keys:
            cached = await self._store.get(key)
            if cached is not None:
                return cached

        mapping = await self._load_from_edgar()
        await self._store.put_many(mapping)

        for key in keys:
            if key in mapping:
                return mapping[key]
        raise EdgarError(f"Unknown ticker: {ticker!r}")
