"""FastAPI application for SEC EDGAR filing downloads.

Run with::

    uvicorn app.main:app --port 8080
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import browse as browse_api
from app.api import config as config_api
from app.api import jobs as jobs_api
from app.api import universe as universe_api
from app.db.filing_store import filing_store
from app.db.ticker_store import ticker_store
from app.messaging.filing_publisher import filing_event_publisher
from app.models import FilingsResponse
from app.startup import initialize_runtime

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await initialize_runtime()
    yield
    ticker_store.close()
    filing_store.close()
    await filing_event_publisher.aclose()


app = FastAPI(
    title="SEC EDGAR Filings",
    version="0.4.0",
    description=(
        "API and UI for S&P 500 filing downloads: primary SEC documents on "
        "disk, metadata in MongoDB, and Kafka events for downstream consumers."
    ),
    lifespan=lifespan,
)

app.include_router(jobs_api.router)
app.include_router(universe_api.router)
app.include_router(config_api.router)
app.include_router(browse_api.router)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.get("/browse")
async def browse_page() -> FileResponse:
    browse_path = STATIC_DIR / "browse.html"
    if not browse_path.is_file():
        raise HTTPException(status_code=404, detail="Browse UI not found")
    return FileResponse(browse_path)


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
