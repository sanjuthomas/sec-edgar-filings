"""FastAPI application for SEC EDGAR filing downloads.

Run with::

    uvicorn app.main:app --port 8080
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.db.filing_store import filing_store
from app.db.ticker_store import ticker_store
from app.messaging.filing_publisher import filing_event_publisher
from app.models import FilingsResponse
from app.startup import initialize_runtime


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await initialize_runtime()
    yield
    ticker_store.close()
    filing_store.close()
    await filing_event_publisher.aclose()


app = FastAPI(
    title="SEC EDGAR Filings API",
    version="0.2.0",
    description=(
        "Given a ticker, returns metadata for SEC filings downloaded to local "
        "storage."
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/filings/{ticker}", response_model=FilingsResponse)
async def get_filings(ticker: str) -> FilingsResponse:
    """Return stored filing metadata for ``ticker``."""

    normalized = ticker.strip().upper()
    filings = await filing_store.get_by_ticker(normalized)
    if not filings:
        raise HTTPException(
            status_code=404,
            detail=f"No filings found for ticker {normalized!r}",
        )

    company_name = filings[0].company_name
    return FilingsResponse(
        ticker=normalized,
        company_name=company_name,
        count=len(filings),
        filings=filings,
    )
