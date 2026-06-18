"""Read-only browse API for inspecting stored ticker data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.browse.filesystem import list_ticker_filesystem, normalize_ticker
from app.config import settings
from app.db.filing_store import filing_store
from app.models import MongoBrowseResult, TickerBrowseResponse

router = APIRouter(prefix="/api/browse", tags=["browse"])


@router.get("/{ticker}", response_model=TickerBrowseResponse)
async def browse_ticker(ticker: str) -> TickerBrowseResponse:
    """Return MongoDB filing metadata and on-disk files for ``ticker``."""

    try:
        normalized = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filings = await filing_store.get_by_ticker(normalized)
    filesystem = list_ticker_filesystem(normalized)
    company_name = filings[0].company_name if filings else None

    return TickerBrowseResponse(
        ticker=normalized,
        company_name=company_name,
        mongo=MongoBrowseResult(
            collection=settings.mongo_filing_metadata_collection,
            count=len(filings),
            filings=filings,
        ),
        filesystem=filesystem,
    )
