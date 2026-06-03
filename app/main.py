"""FastAPI application for SEC EDGAR filing analysis (buyback detection).

Run with::

    uvicorn app.main:app --port 8080
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query

from app.analysis.extractor import (
    EVENT_NEW_AUTHORIZATION,
    EVENT_REFERENCE,
    find_buyback_matches,
    html_to_text,
)
from app.config import settings
from app.db.document_store import document_store
from app.db.ticker_store import ticker_store
from app.edgar.client import EdgarClient, EdgarError
from app.edgar.filings import fetch_filing_document_urls, fetch_recent_filings
from app.edgar.tickers import TickerResolver
from app.models import BuybackAnnouncement, BuybackResponse, Filing

# A new authorization disclosed in a periodic report (10-K/10-Q) should be
# contemporaneous with that report; older board-action dates indicate the
# filing is merely restating a previously announced program. ~one fiscal
# quarter plus filing lag.
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

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # Release the Mongo connection pools on shutdown.
    ticker_store.close()
    document_store.close()


app = FastAPI(
    title="SEC EDGAR Filings API",
    version="0.1.0",
    description=(
        "Given a ticker, finds share buyback / repurchase announcements made "
        "to the SEC in the last %d days." % settings.lookback_days
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _scan_document(
    client: EdgarClient,
    filing: Filing,
    document_url: str,
    *,
    ticker: str,
    cik: str,
) -> list[BuybackAnnouncement]:
    """Download one document and extract buyback announcements from it.

    Results are served from Mongo when this document URL was scanned before;
    otherwise the filing is downloaded, parsed, and cached (including empty
    results). Transient download failures are not cached.
    """

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
        event_type = _refine_event_type(filing, match)
        is_new = event_type == EVENT_NEW_AUTHORIZATION
        announcements.append(
            BuybackAnnouncement(
                event_type=event_type,
                announcement_date=_announcement_date(
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


async def _scan_filing(
    client: EdgarClient, filing: Filing, *, ticker: str, cik: str
) -> list[BuybackAnnouncement]:
    """Scan every narrative document in a filing and extract announcements.

    Announcements (especially new authorizations) are often disclosed in an
    exhibit -- e.g. an earnings press release -- rather than the primary
    document, so we scan all of them.
    """

    document_urls = await fetch_filing_document_urls(client, filing)
    scanned = await asyncio.gather(
        *(
            _scan_document(client, filing, url, ticker=ticker, cik=cik)
            for url in document_urls
        )
    )
    return [ann for group in scanned for ann in group]


def _restates_historical_program(context: str, filing_date: date) -> bool:
    """True when the snippet refers to a program authorized long ago."""

    for m in _SINCE_DATE_RE.finditer(context):
        month_name = m.group(1).lower()
        year = int(m.group(2))
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


def _refine_event_type(filing: Filing, match) -> str:
    """Confirm or downgrade an extractor 'new authorization' using the filing.

    8-Ks are the vehicle for announcing buybacks, so an authorization detected
    there is accepted when it carries a parsed dollar figure. Periodic reports
    (10-K/10-Q) routinely re-describe an existing program, so for those we
    require:

    1. a concrete authorization amount (filters boilerplate such as "we have
       adopted a share repurchase program" that carries no dollar figure); and
    2. either new/expanded-authorization language, or a board-action date that
       is both close to the filing date and distinct from the reporting-period
       end (a date equal to the period end is the reporting boundary, not a
       board action).

    Anything else is a restatement of an existing program and is downgraded to
    a reference.
    """

    if match.event_type != EVENT_NEW_AUTHORIZATION:
        return EVENT_REFERENCE

    context_lower = match.amount_context.lower()
    if "repurchase levels" in context_lower:
        return EVENT_REFERENCE
    if _restates_historical_program(match.amount_context, filing.filing_date):
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


def _announcement_date(
    filing: Filing, event_type: str, authorization_date: date | None
) -> date:
    """Best estimate of when the buyback was announced.

    For a new authorization we prefer the board authorization date parsed from
    the text; failing that, an 8-K's period-of-report is the event date. We fall
    back to the SEC filing date in all other cases.
    """

    if event_type == EVENT_NEW_AUTHORIZATION:
        if authorization_date is not None:
            return authorization_date
        if filing.form.upper() == "8-K" and filing.report_date is not None:
            return filing.report_date
    return filing.filing_date


def _dedupe_authorizations(
    announcements: list[BuybackAnnouncement],
) -> list[BuybackAnnouncement]:
    """Collapse repeated mentions of the same authorization.

    The same authorization is re-described in later filings, and within a
    single filing it is picked up by several overlapping buyback phrases (e.g.
    "repurchase program" and "authority to repurchase") across multiple
    documents (primary + exhibits) that may even parse slightly different
    amounts or dates. We therefore collapse in two passes:

    1. within a filing, down to a single announcement -- a filing announces a
       given program once, so we keep the hit with the largest (most complete)
       parsed amount; then
    2. across filings, by authorization amount -- the same dollar authorization
       re-described in later 10-Q/10-K filings counts once, keeping the
       earliest filing (the original announcement in the window).
    """

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


def _dedupe_references(
    announcements: list[BuybackAnnouncement],
) -> list[BuybackAnnouncement]:
    """Collapse multiple reference hits from the same filing.

    A filing that merely refers to an existing program typically trips several
    overlapping phrases across its documents (primary + exhibits); surfacing one
    reference per filing is sufficient. Documents of one filing share a
    directory, so we key on the filing-directory portion of the document URL.
    """

    seen: dict[str, BuybackAnnouncement] = {}
    for ann in sorted(announcements, key=lambda a: a.filing_date):
        filing_key = ann.filing_url.rsplit("/", 1)[0]
        seen.setdefault(filing_key, ann)
    return list(seen.values())


@app.get("/api/buybacks/{ticker}", response_model=BuybackResponse)
async def get_buybacks(
    ticker: str,
    include_references: bool = False,
    lookback_days: int | None = Query(
        default=None,
        ge=1,
        le=1825,
        description=(
            "How many days back to search for filings (max 1825, i.e. 5 "
            "years). Defaults to %d when omitted." % settings.lookback_days
        ),
    ),
) -> BuybackResponse:
    """Return buyback announcements for ``ticker`` over the lookback window.

    By default only distinct *new authorizations* are returned, with repeated
    references to the same program collapsed. Set ``include_references=true`` to
    also return reference/execution mentions. ``lookback_days`` overrides how
    far back filings are searched (defaults to the configured window).
    """

    effective_lookback = (
        lookback_days
        if isinstance(lookback_days, int)
        else settings.lookback_days
    )

    async with EdgarClient() as client:
        resolver = TickerResolver(client)
        try:
            cik, company_name = await resolver.resolve(ticker)
        except EdgarError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            filings = await fetch_recent_filings(
                client, cik, lookback_days=effective_lookback
            )
        except EdgarError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        normalized_ticker = ticker.upper()
        scanned = await asyncio.gather(
            *(
                _scan_filing(
                    client, filing, ticker=normalized_ticker, cik=cik
                )
                for filing in filings
            )
        )

    all_matches = [a for group in scanned for a in group]
    new_authorizations = _dedupe_authorizations(
        [a for a in all_matches if a.event_type == EVENT_NEW_AUTHORIZATION]
    )
    references = _dedupe_references(
        [a for a in all_matches if a.event_type != EVENT_NEW_AUTHORIZATION]
    )

    returned = list(new_authorizations)
    if include_references:
        returned += references
    returned.sort(key=lambda a: a.announcement_date, reverse=True)

    return BuybackResponse(
        ticker=ticker.upper(),
        cik=cik,
        company_name=company_name,
        lookback_days=effective_lookback,
        count=len(returned),
        new_authorization_count=len(new_authorizations),
        reference_count=len(references),
        announcements=returned,
    )
