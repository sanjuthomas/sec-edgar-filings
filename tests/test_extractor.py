"""Unit tests for the buyback text extractor."""

from __future__ import annotations

from datetime import date

from app.analysis.extractor import (
    EVENT_NEW_AUTHORIZATION,
    EVENT_REFERENCE,
    Match,
    find_buyback_matches,
    html_to_text,
)


def _by_token(matches: list[Match], token: str) -> Match:
    return next(m for m in matches if m.matched_token == token)


def test_html_to_text_strips_markup_and_scripts():
    html = (
        "<html><head><style>.x{}</style></head>"
        "<body><p>Hello&nbsp;<b>world</b></p>"
        "<script>var a = 1;</script></body></html>"
    )
    text = html_to_text(html)
    assert "Hello world" in text
    assert "var a" not in text
    assert ".x{}" not in text


def test_finds_share_repurchase_program_with_billion_amount():
    text = (
        "On March 11, 2025, the board authorized a new $25 billion share "
        "repurchase program with no expiration date."
    )
    matches = find_buyback_matches(text)
    m = _by_token(matches, "share repurchase program")
    assert m.authorization_amount == 25_000_000_000.0
    assert m.authorization_amount_text == "$25 billion"
    assert "repurchase program" in m.amount_context.lower()


def test_finds_million_amount_with_decimal():
    text = "The company announced a $500.5 million share repurchase program."
    matches = find_buyback_matches(text)
    m = _by_token(matches, "share repurchase program")
    assert m.authorization_amount == 500_500_000.0


def test_parses_plain_dollar_figure_with_commas():
    text = "Pursuant to the board authorized repurchase of $1,000,000,000."
    matches = find_buyback_matches(text)
    m = _by_token(matches, "board authorized repurchase")
    assert m.authorization_amount == 1_000_000_000.0


def test_no_amount_returns_none_but_keeps_context():
    text = "The company maintains an ongoing share repurchase program."
    matches = find_buyback_matches(text)
    m = _by_token(matches, "share repurchase program")
    assert m.authorization_amount is None
    assert m.authorization_amount_text is None
    assert "share repurchase program" in m.amount_context.lower()


def test_no_token_match_returns_empty():
    text = "This filing discusses dividends and capital expenditures only."
    assert find_buyback_matches(text) == []


def test_whitespace_tolerant_token_matching():
    text = "A new share   repurchase\nprogram of $10 million was approved."
    matches = find_buyback_matches(text)
    m = _by_token(matches, "share repurchase program")
    assert m.authorization_amount == 10_000_000.0


def test_authorization_amount_preferred_over_unrelated_figure():
    text = (
        "The dividend was $2 per share. Separately, the board approved a "
        "stock buyback authorization of $3 billion."
    )
    matches = find_buyback_matches(text)
    m = _by_token(matches, "stock buyback authorization")
    assert m.event_type == EVENT_NEW_AUTHORIZATION
    assert m.authorization_amount == 3_000_000_000.0


def test_new_authorization_detected_with_board_action_and_date():
    text = (
        "On March 11, 2025, the Board of Directors authorized a new $25 "
        "billion share repurchase program."
    )
    m = _by_token(find_buyback_matches(text), "share repurchase program")
    assert m.event_type == EVENT_NEW_AUTHORIZATION
    assert m.authorization_date == date(2025, 3, 11)
    assert m.authorization_amount == 25_000_000_000.0


def test_quarterly_execution_text_is_a_reference():
    text = (
        "Share Repurchase Program During the six months ended March 28, "
        "2026, the Company repurchased 135 million shares of its common "
        "stock for $36.0 billion. The Company's share repurchase program "
        "does not obligate the Company to acquire a minimum amount of shares."
    )
    m = _by_token(find_buyback_matches(text), "share repurchase program")
    assert m.event_type == EVENT_REFERENCE
    # We do not treat the execution amount as an authorization amount.
    assert m.authorization_amount is None


def test_distinct_new_authorizations_are_kept_separately():
    text = (
        "On January 5, 2025, the board authorized a $5 billion share "
        "repurchase program. On August 5, 2025, the board authorized an "
        "additional $7 billion share repurchase program."
    )
    repurchase = [
        m
        for m in find_buyback_matches(text)
        if m.matched_token == "share repurchase program"
    ]
    amounts = {m.authorization_amount for m in repurchase}
    assert amounts == {5_000_000_000.0, 7_000_000_000.0}


def test_board_approved_during_quarter_is_new_authorization():
    # Mirrors a Goldman Sachs earnings-release exhibit: an explicit board
    # approval with an amount must win over the "during the" reference phrase.
    text = (
        "During the quarter, the Board approved a share repurchase program "
        "authorizing repurchases of up to $40 billion of common stock."
    )
    m = _by_token(find_buyback_matches(text), "share repurchase program")
    assert m.event_type == EVENT_NEW_AUTHORIZATION
    assert m.authorization_amount == 40_000_000_000.0


def test_program_noun_near_dividend_is_not_new_authorization():
    # Mirrors a false positive: "repurchase authorization" is a noun phrase
    # describing an existing program; a nearby dividend figure must not make it
    # look board-approved.
    text = (
        "Announced a $0.50 additional increase in the quarterly dividend to "
        "$4.50 per share. The firm returned excess capital via buybacks and "
        "had roughly $32 billion of capacity under its current share "
        "repurchase authorization."
    )
    m = _by_token(find_buyback_matches(text), "repurchase authorization")
    assert m.event_type == EVENT_REFERENCE


def test_implausibly_small_amount_is_discarded():
    # "$25" stranded from "$25 Billion" (scale word lost in formatting) must
    # not be reported as an authorization amount.
    text = "The board approved a new repurchase program of $25 this quarter."
    m = _by_token(find_buyback_matches(text), "repurchase program")
    assert m.authorization_amount is None


def test_identical_authorization_repeated_in_doc_is_deduped():
    text = (
        "On March 11, 2025, the board authorized a $9 billion share "
        "repurchase program. As previously disclosed, on March 11, 2025 the "
        "board authorized a $9 billion share repurchase program."
    )
    repurchase = [
        m
        for m in find_buyback_matches(text)
        if m.matched_token == "share repurchase program"
    ]
    assert len(repurchase) == 1
