"""Fetch and parse the current S&P 500 constituent list.

The list is sourced from Wikipedia's maintained table of S&P 500 companies.
That page is updated when index membership changes, so a nightly refresh
picks up additions and removals without a paid data feed.
"""

from __future__ import annotations

import os

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import Sp500Constituent

SP500_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def wikipedia_user_agent() -> str:
    """Return a User-Agent string Wikipedia will accept.

    Wikipedia rejects generic placeholders. Reuse ``SEC_USER_AGENT`` when it
    includes a real contact, otherwise honor ``WIKIPEDIA_USER_AGENT``.
    """

    sec_ua = os.environ.get("SEC_USER_AGENT")
    if sec_ua and "set SEC_USER_AGENT" not in sec_ua:
        return sec_ua
    override = os.environ.get("WIKIPEDIA_USER_AGENT")
    if override:
        return override
    ua = settings.user_agent
    if "set SEC_USER_AGENT" not in ua:
        return ua
    raise ValueError(
        "Set SEC_USER_AGENT or WIKIPEDIA_USER_AGENT before fetching the "
        "S&P 500 list from Wikipedia."
    )


def parse_sp500_constituents(html: str) -> list[Sp500Constituent]:
    """Parse the primary constituents table from Wikipedia HTML."""

    soup = BeautifulSoup(html, "lxml")
    table = _find_constituents_table(soup)
    if table is None:
        raise ValueError("S&P 500 constituents table not found in HTML")

    constituents: list[Sp500Constituent] = []
    seen: set[str] = set()
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        ticker = cells[0].get_text(strip=True).upper()
        company_name = cells[1].get_text(strip=True)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        constituents.append(
            Sp500Constituent(ticker=ticker, company_name=company_name)
        )

    if not constituents:
        raise ValueError("S&P 500 constituents table contained no rows")
    return constituents


def _find_constituents_table(soup: BeautifulSoup):
    for table in soup.find_all("table", class_="wikitable"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if headers and headers[0] == "Symbol":
            return table
    return None


async def fetch_sp500_constituents(
    client: httpx.AsyncClient | None = None,
) -> list[Sp500Constituent]:
    """Download and parse the current S&P 500 list from Wikipedia."""

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": wikipedia_user_agent()},
            follow_redirects=True,
        )
    try:
        response = await client.get(SP500_WIKIPEDIA_URL)
        response.raise_for_status()
        return parse_sp500_constituents(response.text)
    finally:
        if owns_client:
            await client.aclose()
