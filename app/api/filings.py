"""Filing metadata API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.browse.filesystem import delete_ticker_filesystem, normalize_ticker
from app.config import settings
from app.db.filing_store import filing_store
from app.db.universe_store import universe_store
from app.models import DeleteFilingsByTickerResponse, FilingsResponse

router = APIRouter(prefix="/api/filings", tags=["filings"])


@router.get("/{ticker}", response_model=FilingsResponse)
async def get_filings(ticker: str) -> FilingsResponse:
    """Return stored filing metadata for ``ticker``."""

    try:
        normalized = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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


@router.delete("/{ticker}", response_model=DeleteFilingsByTickerResponse)
async def delete_filings_by_ticker(ticker: str) -> DeleteFilingsByTickerResponse:
    """Delete all filing metadata documents for ``ticker``."""

    try:
        normalized = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    deleted_count = await filing_store.delete_by_ticker(normalized)
    accession_dirs_deleted, files_deleted = delete_ticker_filesystem(normalized)
    if deleted_count == 0 and files_deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No filings found in {settings.mongo_filing_metadata_collection!r} "
                f"or on disk for ticker {normalized!r}"
            ),
        )

    universe_status_reset = await universe_store.reset_download_status_for_ticker(
        normalized
    )
    return DeleteFilingsByTickerResponse(
        ticker=normalized,
        collection=settings.mongo_filing_metadata_collection,
        deleted_count=deleted_count,
        files_deleted=files_deleted,
        accession_dirs_deleted=accession_dirs_deleted,
        universe_status_reset=universe_status_reset,
    )
