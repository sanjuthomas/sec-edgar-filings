"""Lookback window helpers for S&P batch jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings


def effective_sp500_lookback_days(
    *,
    mode: str,
    last_scan_at: datetime | None,
    lookback_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """Choose how far back to scan one S&P ticker.

    Incremental runs use a short default window, widened to cover the gap
    since the last successful scan. Tickers that have never been scanned use
    the backfill window.
    """

    if lookback_days is not None:
        return lookback_days
    if mode == "backfill":
        return settings.sp500_backfill_lookback_days
    if last_scan_at is None:
        return settings.sp500_backfill_lookback_days

    now = now or datetime.now(UTC)
    if last_scan_at.tzinfo is None:
        last_scan_at = last_scan_at.replace(tzinfo=UTC)
    days_since = max((now - last_scan_at).days, 0)
    return max(settings.sp500_incremental_lookback_days, days_since + 1)
