"""S&P 500 universe status API."""

from __future__ import annotations

from fastapi import APIRouter

from app.db.universe_store import universe_store
from app.models import Sp500StatusSummary, Sp500TickerStatus

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("/sp500/status", response_model=Sp500StatusSummary)
async def get_sp500_status() -> Sp500StatusSummary:
    tickers = await universe_store.list_ticker_statuses(active_only=True)
    downloaded_ok = sum(1 for t in tickers if t.last_download_status == "ok")
    downloaded_error = sum(1 for t in tickers if t.last_download_status == "error")
    never_downloaded = sum(1 for t in tickers if t.last_download_status is None)
    return Sp500StatusSummary(
        active_count=len(tickers),
        downloaded_ok=downloaded_ok,
        downloaded_error=downloaded_error,
        never_downloaded=never_downloaded,
        tickers=tickers,
    )


@router.get("/sp500/{ticker}/status", response_model=Sp500TickerStatus | None)
async def get_ticker_status(ticker: str) -> Sp500TickerStatus | None:
    normalized = ticker.strip().upper()
    tickers = await universe_store.list_ticker_statuses(active_only=False)
    for item in tickers:
        if item.ticker == normalized:
            return item
    return None
