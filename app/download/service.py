"""Download SEC filings and persist metadata."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.db.filing_store import FilingStore, filing_store
from app.edgar.client import EdgarClient
from app.messaging.filing_publisher import FilingEventPublisher, filing_event_publisher
from app.edgar.filings import fetch_recent_filings
from app.edgar.tickers import TickerResolver
from app.models import DownloadTickerResult, Filing, FilingMetadata

logger = logging.getLogger(__name__)

_PDF_EXT = ".pdf"


def _filing_dir(base: Path, ticker: str, accession_number: str) -> Path:
    accession_nodashes = accession_number.replace("-", "")
    return base / ticker.upper() / accession_nodashes


async def _download_primary_document(
    client: EdgarClient,
    filing: Filing,
    dest_dir: Path,
) -> tuple[Path, bool]:
    """Download the filing's primary document to ``dest_dir``.

    Returns ``(local_path, was_written)`` where ``was_written`` is true only
    when bytes were freshly fetched from EDGAR and written to disk.
    """

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filing.primary_document
    if dest_path.exists():
        return dest_path, False

    lower = filing.primary_document.lower()
    if lower.endswith(_PDF_EXT):
        content = await client.get_bytes(filing.document_url)
        dest_path.write_bytes(content)
    else:
        content = await client.get_text(filing.document_url)
        dest_path.write_text(content, encoding="utf-8")
    return dest_path, True


async def download_filing(
    client: EdgarClient,
    filing: Filing,
    *,
    ticker: str,
    company_name: str,
    store: FilingStore | None = None,
    publisher: FilingEventPublisher | None = None,
    base_dir: Path | None = None,
) -> FilingMetadata | None:
    """Download one filing if not already stored. Returns metadata or None if skipped."""

    store = store or filing_store
    publisher = publisher or filing_event_publisher
    base_dir = base_dir or Path(settings.edgar_download_base)

    if await store.exists(filing.accession_number):
        return None

    dest_dir = _filing_dir(base_dir, ticker, filing.accession_number)
    local_path, _was_written = await _download_primary_document(
        client, filing, dest_dir
    )
    metadata = FilingMetadata(
        ticker=ticker.upper(),
        company_name=company_name,
        filing_date=filing.filing_date,
        form=filing.form,
        accession_number=filing.accession_number,
        local_path=str(local_path),
        document_url=filing.document_url,
        downloaded_at=datetime.now(UTC),
    )
    await publisher.publish_filing_downloaded(metadata)
    await store.put(metadata)
    return metadata


async def download_ticker(
    client: EdgarClient,
    ticker: str,
    *,
    lookback_days: int | None = None,
    store: FilingStore | None = None,
    base_dir: Path | None = None,
) -> DownloadTickerResult:
    """Download recent filings for ``ticker`` within the lookback window."""

    store = store or filing_store
    if not isinstance(lookback_days, int):
        lookback_days = settings.lookback_days

    resolver = TickerResolver(client)
    cik, company_name = await resolver.resolve(ticker)
    normalized_ticker = ticker.strip().upper()

    filings = await fetch_recent_filings(
        client, cik, lookback_days=lookback_days
    )

    downloaded = 0
    skipped = 0
    for filing in filings:
        try:
            result = await download_filing(
                client,
                filing,
                ticker=normalized_ticker,
                company_name=company_name,
                store=store,
                base_dir=base_dir,
            )
        except Exception as exc:
            logger.error(
                "Failed to download %s filing %s: %s",
                normalized_ticker,
                filing.accession_number,
                exc,
            )
            continue
        if result is None:
            skipped += 1
        else:
            downloaded += 1
            logger.info(
                "Downloaded %s %s %s -> %s",
                normalized_ticker,
                filing.form,
                filing.filing_date,
                result.local_path,
            )

    return DownloadTickerResult(
        ticker=normalized_ticker,
        cik=cik,
        company_name=company_name,
        lookback_days=lookback_days,
        filings_found=len(filings),
        filings_downloaded=downloaded,
        filings_skipped=skipped,
    )
