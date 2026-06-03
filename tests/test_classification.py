"""Tests for filing-aware classification refinement in the API layer."""

from __future__ import annotations

from datetime import date

from app.analysis.extractor import (
    EVENT_NEW_AUTHORIZATION,
    EVENT_REFERENCE,
    Match,
)
from app.main import _dedupe_authorizations, _refine_event_type
from app.models import BuybackAnnouncement, Filing


def _announcement(
    *,
    filing_url: str,
    amount: float | None,
    announcement_date: date,
    filing_date: date,
) -> BuybackAnnouncement:
    return BuybackAnnouncement(
        event_type=EVENT_NEW_AUTHORIZATION,
        announcement_date=announcement_date,
        report_date=None,
        authorization_amount=amount,
        authorization_amount_text=None,
        amount_context="...",
        matched_token="repurchase program",
        form="8-K",
        filing_date=filing_date,
        filing_url=filing_url,
    )


def _filing(form: str, filing_date: date, report_date: date | None) -> Filing:
    return Filing(
        form=form,
        filing_date=filing_date,
        report_date=report_date,
        accession_number="0000000000-00-000000",
        primary_document="doc.htm",
        document_url="https://example.com/doc.htm",
    )


def _match(
    *,
    event_type: str = EVENT_NEW_AUTHORIZATION,
    amount: float | None = 5_000_000_000.0,
    authorization_date: date | None = None,
    expansion: bool = False,
) -> Match:
    return Match(
        matched_token="share repurchase program",
        amount_context="...",
        authorization_amount=amount,
        authorization_amount_text="$5 billion" if amount else None,
        event_type=event_type,
        authorization_date=authorization_date,
        has_expansion_language=expansion,
    )


def test_extractor_reference_stays_reference():
    filing = _filing("10-Q", date(2026, 1, 30), date(2025, 12, 27))
    assert (
        _refine_event_type(filing, _match(event_type=EVENT_REFERENCE))
        == EVENT_REFERENCE
    )


def test_8k_authorization_always_accepted():
    filing = _filing("8-K", date(2025, 5, 1), date(2025, 5, 1))
    assert (
        _refine_event_type(filing, _match(amount=None))
        == EVENT_NEW_AUTHORIZATION
    )


def test_periodic_report_without_amount_is_reference():
    # Mirrors the HPQ boilerplate false positive: expansion language but no
    # concrete authorization amount.
    filing = _filing("10-K", date(2025, 12, 10), date(2025, 10, 31))
    match = _match(amount=None, expansion=True)
    assert _refine_event_type(filing, match) == EVENT_REFERENCE


def test_period_end_date_is_not_a_board_action():
    # Mirrors the HPQ fiscal-year-end false positive: the only date equals the
    # reporting period end and there is no expansion language.
    filing = _filing("10-K", date(2025, 12, 10), date(2025, 10, 31))
    match = _match(
        amount=800_000_000.0,
        authorization_date=date(2025, 10, 31),
        expansion=False,
    )
    assert _refine_event_type(filing, match) == EVENT_REFERENCE


def test_periodic_report_with_amount_and_expansion_is_new():
    filing = _filing("10-Q", date(2025, 6, 25), date(2025, 5, 30))
    match = _match(amount=10_000_000_000.0, expansion=True)
    assert _refine_event_type(filing, match) == EVENT_NEW_AUTHORIZATION


def test_periodic_report_with_recent_distinct_board_date_is_new():
    filing = _filing("10-Q", date(2025, 6, 25), date(2025, 5, 30))
    match = _match(
        amount=10_000_000_000.0,
        authorization_date=date(2025, 5, 12),
        expansion=False,
    )
    assert _refine_event_type(filing, match) == EVENT_NEW_AUTHORIZATION


def test_periodic_report_with_stale_board_date_is_reference():
    filing = _filing("10-K", date(2025, 12, 10), date(2025, 10, 31))
    match = _match(
        amount=10_000_000_000.0,
        authorization_date=date(2024, 1, 1),
        expansion=False,
    )
    assert _refine_event_type(filing, match) == EVENT_REFERENCE


def test_dedupe_collapses_same_filing_to_best_amount():
    # The same announcement parsed in two documents of one filing: a complete
    # $25B amount and a stranded null amount. Only the $25B should survive.
    base = "https://sec.gov/Archives/edgar/data/1/000111/"
    anns = [
        _announcement(
            filing_url=base + "primary.htm",
            amount=25_000_000_000.0,
            announcement_date=date(2026, 4, 21),
            filing_date=date(2026, 4, 21),
        ),
        _announcement(
            filing_url=base + "ex991.htm",
            amount=None,
            announcement_date=date(2026, 4, 21),
            filing_date=date(2026, 4, 21),
        ),
    ]
    result = _dedupe_authorizations(anns)
    assert len(result) == 1
    assert result[0].authorization_amount == 25_000_000_000.0


def test_dedupe_collapses_same_filing_different_parsed_dates():
    # Two exhibits of one filing parse the same $40B program but pick different
    # nearby dates; they must still collapse to a single authorization.
    base = "https://sec.gov/Archives/edgar/data/2/000222/"
    anns = [
        _announcement(
            filing_url=base + "ex991.htm",
            amount=40_000_000_000.0,
            announcement_date=date(2025, 5, 30),
            filing_date=date(2025, 4, 14),
        ),
        _announcement(
            filing_url=base + "ex992.htm",
            amount=40_000_000_000.0,
            announcement_date=date(2025, 4, 14),
            filing_date=date(2025, 4, 14),
        ),
    ]
    assert len(_dedupe_authorizations(anns)) == 1
