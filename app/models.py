"""Pydantic models for API responses and internal data passing."""

from __future__ import annotations

from datetime import date, datetime

from typing import Literal

from pydantic import BaseModel, Field

JobType = Literal["single_ticker", "batch_download", "full_reload"]
JobStatus = Literal["pending", "running", "completed", "failed"]


class Filing(BaseModel):
    """A single SEC filing selected for scanning."""

    form: str
    filing_date: date
    report_date: date | None = None
    accession_number: str
    primary_document: str
    document_url: str


class BuybackAnnouncement(BaseModel):
    """One buyback-related match found within a filing."""

    event_type: str = Field(
        ...,
        description=(
            "'new_authorization' if the filing announces a new/expanded "
            "buyback authorization, or 'reference' if it merely refers to an "
            "existing program (e.g. quarterly execution disclosure)."
        ),
    )
    announcement_date: date = Field(
        ...,
        description=(
            "The board authorization date when detected, otherwise the SEC "
            "filing date."
        ),
    )
    authorization_date: date | None = Field(
        None,
        description="Board authorization date parsed from the filing text.",
    )
    report_date: date | None = Field(
        None, description="Filing period of report, when provided by EDGAR."
    )
    authorization_amount: float | None = Field(
        None, description="Parsed authorization amount in USD, or null."
    )
    authorization_amount_text: str | None = Field(
        None, description="Raw amount text as it appeared in the filing."
    )
    amount_context: str = Field(
        ..., description="Snippet of surrounding text around the match."
    )
    matched_token: str
    form: str
    filing_date: date
    filing_url: str


class Sp500Constituent(BaseModel):
    """One S&P 500 member as published in the index constituents table."""

    ticker: str
    company_name: str


class UniverseRefreshResult(BaseModel):
    """Summary of a universe refresh run."""

    source: str
    source_url: str
    refreshed_at: datetime
    active_count: int
    added: list[str]
    removed: list[str]


class ScanTickerResult(BaseModel):
    """Outcome of scanning one ticker over a lookback window."""

    ticker: str
    cik: str
    company_name: str
    lookback_days: int
    filings_scanned: int
    new_authorizations: list[BuybackAnnouncement]
    references: list[BuybackAnnouncement]

    @property
    def new_authorization_count(self) -> int:
        return len(self.new_authorizations)

    @property
    def reference_count(self) -> int:
        return len(self.references)


class FilingMetadata(BaseModel):
    """Metadata for one downloaded SEC filing."""

    ticker: str
    company_name: str
    filing_date: date
    form: str
    accession_number: str
    local_path: str
    document_url: str
    downloaded_at: datetime | None = None


class FilingsResponse(BaseModel):
    """Top-level API response listing filings for a ticker."""

    ticker: str
    company_name: str | None = None
    count: int
    filings: list[FilingMetadata]


class DownloadTickerResult(BaseModel):
    """Outcome of downloading filings for one ticker."""

    ticker: str
    cik: str
    company_name: str
    lookback_days: int
    filings_found: int
    filings_downloaded: int
    filings_skipped: int


class Sp500DownloadResult(BaseModel):
    """Summary of one S&P 500 download batch run."""

    mode: str
    universe_refresh: UniverseRefreshResult | None = None
    tickers_total: int
    tickers_processed: int
    tickers_failed: int
    failed_tickers: list[str]
    total_filings_downloaded: int
    total_filings_skipped: int


class JobProgress(BaseModel):
    """Live or final status for a background download job."""

    job_id: str
    job_type: JobType
    status: JobStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ticker: str | None = None
    current_ticker: str | None = None
    lookback_days: int | None = None
    tickers_total: int = 0
    tickers_completed: int = 0
    tickers_failed: int = 0
    failed_tickers: list[str] = Field(default_factory=list)
    total_filings_downloaded: int = 0
    total_filings_skipped: int = 0
    message: str | None = None
    error: str | None = None
    result: DownloadTickerResult | Sp500DownloadResult | None = None


class Sp500TickerStatus(BaseModel):
    """Per-ticker download state from the universe store."""

    ticker: str
    company_name: str | None = None
    active: bool = True
    last_download_at: datetime | None = None
    last_download_status: str | None = None
    last_download_lookback_days: int | None = None
    last_download_filings_found: int | None = None
    last_download_filings_downloaded: int | None = None
    last_download_filings_skipped: int | None = None
    last_download_error: str | None = None


class Sp500StatusSummary(BaseModel):
    """Aggregate S&P 500 download coverage."""

    active_count: int
    downloaded_ok: int
    downloaded_error: int
    never_downloaded: int
    tickers: list[Sp500TickerStatus]


class RuntimeConfig(BaseModel):
    """Read-only effective runtime settings for the UI."""

    kafka_enabled: bool
    kafka_bootstrap_servers: str
    kafka_filing_downloaded_topic: str
    edgar_download_base: str
    ticker_rate_limit_seconds: float
    default_lookback_days: int
    sp500_download_lookback_days: int
