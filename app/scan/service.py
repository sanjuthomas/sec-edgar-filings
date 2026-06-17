"""Scan EDGAR filings for buyback announcements.

Shared by the HTTP API and the S&P nightly batch job so scan logic lives in
one place.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

from app.analysis.extractor import (
    EVENT_NEW_AUTHORIZATION,
    EVENT_REFERENCE,
    find_buyback_matches,
    html_to_text,
)
from app.config import settings
from app.db.document_store import document_store
from app.edgar.client import EdgarClient, EdgarError
from app.edgar.filings import fetch_filing_document_urls, fetch_recent_filings
from app.edgar.tickers import TickerResolver
from app.models import BuybackAnnouncement, BuybackResponse, Filing, ScanTickerResult

_CONTEMPORANEOUS_DAYS = 135

_SINCE_DATE_RE = re.compile(
    r"\bsince\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{4})\b",
    re.IGNORECASE,
)
_SINCE_MONTH_INDEX = {
    name.lower(): i
    for i, name in enumerate(
        "January February March April May June July August September October "
        "November December".split(),
        start=1,
    )
}


async def scan_document(
    client: EdgarClient,
    filing: Filing,
    document_url: str,
    *,
    ticker: str,
    cik: str,
) -> list[BuybackAnnouncement]:
    """Download one document and extract buyback announcements from it."""

    cached = await document_store.get(document_url)
    if cached is not None:
        return cached

    try:
        content = await client.get_text(document_url)
    except EdgarError:
        return []

    text = html_to_text(content)
    announcements: list[BuybackAnnouncement] = []
    for match in find_buyback_matches(text):
        event_type = refine_event_type(filing, match)
        is_new = event_type == EVENT_NEW_AUTHORIZATION
        announcements.append(
            BuybackAnnouncement(
                event_type=event_type,
                announcement_date=announcement_date(
                    filing, event_type, match.authorization_date
                ),
                authorization_date=match.authorization_date if is_new else None,
                report_date=filing.report_date,
                authorization_amount=(
                    match.authorization_amount if is_new else None
                ),
                authorization_amount_text=(
                    match.authorization_amount_text if is_new else None
                ),
                amount_context=match.amount_context,
                matched_token=match.matched_token,
                form=filing.form,
                filing_date=filing.filing_date,
                filing_url=document_url,
            )
        )
    await document_store.put(
        document_url, announcements, ticker=ticker, cik=cik
    )
    return announcements


async def scan_filing(
    client: EdgarClient, filing: Filing, *, ticker: str, cik: str
) -> list[BuybackAnnouncement]:
    """Scan every narrative document in a filing and extract announcements."""

    document_urls = await fetch_filing_document_urls(client, filing)
    scanned = await asyncio.gather(
        *(
            scan_document(client, filing, url, ticker=ticker, cik=cik)
            for url in document_urls
        )
    )
    return [ann for group in scanned for ann in group]


async def scan_ticker(
    client: EdgarClient,
    ticker: str,
    *,
    lookback_days: int | None = None,
    resolver: TickerResolver | None = None,
) -> ScanTickerResult:
    """Resolve ``ticker`` and scan its filings within the lookback window."""

    effective_lookback = (
        lookback_days if isinstance(lookback_days, int) else settings.lookback_days
    )
    resolver = resolver or TickerResolver(client)
    cik, company_name = await resolver.resolve(ticker)
    filings = await fetch_recent_filings(
        client, cik, lookback_days=effective_lookback
    )

    normalized_ticker = ticker.upper()
    scanned = await asyncio.gather(
        *(
            scan_filing(client, filing, ticker=normalized_ticker, cik=cik)
            for filing in filings
        )
    )
    all_matches = [a for group in scanned for a in group]
    new_authorizations = dedupe_authorizations(
        [a for a in all_matches if a.event_type == EVENT_NEW_AUTHORIZATION]
    )
    references = dedupe_references(
        [a for a in all_matches if a.event_type != EVENT_NEW_AUTHORIZATION]
    )

    return ScanTickerResult(
        ticker=normalized_ticker,
        cik=cik,
        company_name=company_name,
        lookback_days=effective_lookback,
        filings_scanned=len(filings),
        new_authorizations=new_authorizations,
        references=references,
    )


def build_buyback_response(
    result: ScanTickerResult,
    *,
    include_references: bool = False,
) -> BuybackResponse:
    """Build the public API payload from scan results."""

    returned = list(result.new_authorizations)
    if include_references:
        returned += result.references
    returned.sort(key=lambda a: a.announcement_date, reverse=True)

    return BuybackResponse(
        ticker=result.ticker,
        cik=result.cik,
        company_name=result.company_name,
        lookback_days=result.lookback_days,
        count=len(returned),
        new_authorization_count=result.new_authorization_count,
        reference_count=result.reference_count,
        announcements=returned,
    )


def restates_historical_program(context: str, filing_date: date) -> bool:
    """True when the snippet refers to a program authorized long ago."""

    for match in _SINCE_DATE_RE.finditer(context):
        month_name = match.group(1).lower()
        year = int(match.group(2))
        month = _SINCE_MONTH_INDEX.get(month_name)
        if month is None:
            continue
        try:
            since_date = date(year, month, 1)
        except ValueError:
            continue
        if (filing_date - since_date).days > _CONTEMPORANEOUS_DAYS:
            return True
    return False


def refine_event_type(filing: Filing, match) -> str:
    """Confirm or downgrade an extractor 'new authorization' using the filing."""

    if match.event_type != EVENT_NEW_AUTHORIZATION:
        return EVENT_REFERENCE

    context_lower = match.amount_context.lower()
    if "repurchase levels" in context_lower:
        return EVENT_REFERENCE
    if restates_historical_program(match.amount_context, filing.filing_date):
        return EVENT_REFERENCE

    if filing.form.upper() == "8-K":
        if match.authorization_amount is not None:
            return EVENT_NEW_AUTHORIZATION
        return EVENT_REFERENCE
    if match.authorization_amount is None:
        return EVENT_REFERENCE
    if match.has_expansion_language:
        return EVENT_NEW_AUTHORIZATION
    auth_date = match.authorization_date
    if auth_date is not None and auth_date != filing.report_date:
        gap_days = (filing.filing_date - auth_date).days
        if 0 <= gap_days <= _CONTEMPORANEOUS_DAYS:
            return EVENT_NEW_AUTHORIZATION
    return EVENT_REFERENCE


def announcement_date(
    filing: Filing, event_type: str, authorization_date: date | None
) -> date:
    """Best estimate of when the buyback was announced."""

    if event_type == EVENT_NEW_AUTHORIZATION:
        if authorization_date is not None:
            return authorization_date
        if filing.form.upper() == "8-K" and filing.report_date is not None:
            return filing.report_date
    return filing.filing_date


def dedupe_authorizations(
    announcements: list[BuybackAnnouncement],
) -> list[BuybackAnnouncement]:
    """Collapse repeated mentions of the same authorization."""

    def _amount_key(ann: BuybackAnnouncement) -> float | None:
        return (
            round(ann.authorization_amount, 2)
            if ann.authorization_amount is not None
            else None
        )

    def _amount_rank(ann: BuybackAnnouncement) -> float:
        return ann.authorization_amount if ann.authorization_amount is not None else -1.0

    per_filing: dict[str, BuybackAnnouncement] = {}
    for ann in sorted(announcements, key=lambda a: a.filing_date):
        filing_key = ann.filing_url.rsplit("/", 1)[0]
        current = per_filing.get(filing_key)
        if current is None or _amount_rank(ann) > _amount_rank(current):
            per_filing[filing_key] = ann

    by_amount: dict[float, BuybackAnnouncement] = {}
    without_amount: dict[str, BuybackAnnouncement] = {}
    for ann in sorted(per_filing.values(), key=lambda a: a.filing_date):
        amount_key = _amount_key(ann)
        if amount_key is not None:
            by_amount.setdefault(amount_key, ann)
        else:
            filing_key = ann.filing_url.rsplit("/", 1)[0]
            without_amount.setdefault(filing_key, ann)
    return list(by_amount.values()) + list(without_amount.values())


def dedupe_references(
    announcements: list[BuybackAnnouncement],
) -> list[BuybackAnnouncement]:
    """Collapse multiple reference hits from the same filing."""

    seen: dict[str, BuybackAnnouncement] = {}
    for ann in sorted(announcements, key=lambda a: a.filing_date):
        filing_key = ann.filing_url.rsplit("/", 1)[0]
        seen.setdefault(filing_key, ann)
    return list(seen.values())
