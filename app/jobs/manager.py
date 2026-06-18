"""In-process background job manager for the API."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from app.jobs.download_sp500 import run_download_sp500
from app.jobs.runner import run_full_reload, run_single_ticker_download
from app.models import JobProgress, JobType

logger = logging.getLogger(__name__)


class JobConflictError(Exception):
    """Raised when a new job is requested while another is running."""


class JobManager:
    """Track and run at most one download job at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobProgress] = {}
        self._current_job_id: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def get_job(self, job_id: str) -> JobProgress | None:
        return self._jobs.get(job_id)

    def get_current_job(self) -> JobProgress | None:
        if self._current_job_id is None:
            return None
        return self._jobs.get(self._current_job_id)

    def list_jobs(self, *, limit: int = 20) -> list[JobProgress]:
        jobs = sorted(
            self._jobs.values(),
            key=lambda job: job.started_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return jobs[:limit]

    async def start_single_ticker(self, ticker: str, *, lookback_days: int) -> JobProgress:
        normalized = ticker.strip().upper()
        job = self._create_job(
            job_type="single_ticker",
            lookback_days=lookback_days,
            ticker=normalized,
            tickers_total=1,
            message=f"Downloading {normalized}",
        )
        self._launch(job.job_id, self._run_single_ticker(job.job_id, normalized, lookback_days))
        return job

    async def start_batch_download(
        self,
        *,
        lookback_days: int,
        skip_refresh: bool = False,
    ) -> JobProgress:
        job = self._create_job(
            job_type="batch_download",
            lookback_days=lookback_days,
            message="S&P 500 batch download",
        )
        self._launch(
            job.job_id,
            self._run_batch(job.job_id, lookback_days, skip_refresh),
        )
        return job

    async def start_full_reload(
        self,
        *,
        lookback_days: int,
        skip_refresh: bool = False,
    ) -> JobProgress:
        job = self._create_job(
            job_type="full_reload",
            lookback_days=lookback_days,
            message="Full reload: clearing metadata and re-downloading",
        )
        self._launch(
            job.job_id,
            self._run_full_reload(job.job_id, lookback_days, skip_refresh),
        )
        return job

    def _create_job(
        self,
        *,
        job_type: JobType,
        lookback_days: int,
        ticker: str | None = None,
        tickers_total: int = 0,
        message: str | None = None,
    ) -> JobProgress:
        if self._current_job_id is not None:
            current = self._jobs.get(self._current_job_id)
            if current is not None and current.status in ("pending", "running"):
                raise JobConflictError(
                    f"Job {self._current_job_id} is already {current.status}"
                )

        job_id = uuid.uuid4().hex[:12]
        job = JobProgress(
            job_id=job_id,
            job_type=job_type,
            status="pending",
            ticker=ticker,
            lookback_days=lookback_days,
            tickers_total=tickers_total,
            message=message,
        )
        self._jobs[job_id] = job
        self._current_job_id = job_id
        return job

    def _launch(self, job_id: str, coro) -> None:
        self._task = asyncio.create_task(self._run_wrapper(job_id, coro))

    async def _run_wrapper(self, job_id: str, coro) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        job.started_at = datetime.now(UTC)
        try:
            await coro
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            job.status = "failed"
            job.error = str(exc)
            job.message = "Job failed"
        finally:
            job.completed_at = datetime.now(UTC)
            if self._current_job_id == job_id:
                self._current_job_id = None
            self._task = None

    async def _run_single_ticker(
        self, job_id: str, ticker: str, lookback_days: int
    ) -> None:
        job = self._jobs[job_id]

        async def on_start(current: str, _index: int, total: int) -> None:
            job.current_ticker = current
            job.tickers_total = total

        async def on_complete(_current: str, completed: int, total: int) -> None:
            job.tickers_completed = completed
            job.tickers_total = total

        await on_start(ticker, 0, 1)
        try:
            result = await run_single_ticker_download(ticker, lookback_days=lookback_days)
        except Exception as exc:
            job.tickers_failed = 1
            job.failed_tickers = [ticker]
            raise exc

        await on_complete(ticker, 1, 1)
        job.total_filings_downloaded = result.filings_downloaded
        job.total_filings_skipped = result.filings_skipped
        job.result = result
        job.status = "completed"
        job.message = (
            f"Downloaded {result.filings_downloaded} new filings for {ticker}"
        )

    async def _run_batch(
        self, job_id: str, lookback_days: int, skip_refresh: bool
    ) -> None:
        job = self._jobs[job_id]

        async def on_start(current: str, index: int, total: int) -> None:
            job.current_ticker = current
            job.tickers_total = total
            job.tickers_completed = index

        async def on_complete(current: str, completed: int, total: int) -> None:
            job.current_ticker = current
            job.tickers_completed = completed
            job.tickers_total = total

        summary = await run_download_sp500(
            mode="backfill",
            skip_refresh=skip_refresh,
            lookback_days=lookback_days,
            initialize=False,
            on_ticker_start=on_start,
            on_ticker_complete=on_complete,
        )
        job.tickers_total = summary.tickers_total
        job.tickers_completed = summary.tickers_processed + summary.tickers_failed
        job.tickers_failed = summary.tickers_failed
        job.failed_tickers = summary.failed_tickers
        job.total_filings_downloaded = summary.total_filings_downloaded
        job.total_filings_skipped = summary.total_filings_skipped
        job.result = summary
        job.status = "completed" if summary.tickers_failed == 0 else "failed"
        job.message = (
            f"Batch complete: {summary.tickers_processed} ok, "
            f"{summary.tickers_failed} failed"
        )
        if summary.tickers_failed:
            job.error = f"Failed tickers: {', '.join(summary.failed_tickers)}"

    async def _run_full_reload(
        self, job_id: str, lookback_days: int, skip_refresh: bool
    ) -> None:
        job = self._jobs[job_id]

        async def on_start(current: str, index: int, total: int) -> None:
            job.current_ticker = current
            job.tickers_total = total
            job.tickers_completed = index
            job.message = f"Full reload: processing {current}"

        async def on_complete(current: str, completed: int, total: int) -> None:
            job.current_ticker = current
            job.tickers_completed = completed
            job.tickers_total = total

        deleted_metadata, reset_tickers, summary = await run_full_reload(
            lookback_days=lookback_days,
            skip_refresh=skip_refresh,
            on_ticker_start=on_start,
            on_ticker_complete=on_complete,
        )
        job.tickers_total = summary.tickers_total
        job.tickers_completed = summary.tickers_processed + summary.tickers_failed
        job.tickers_failed = summary.tickers_failed
        job.failed_tickers = summary.failed_tickers
        job.total_filings_downloaded = summary.total_filings_downloaded
        job.total_filings_skipped = summary.total_filings_skipped
        job.result = summary
        job.status = "completed" if summary.tickers_failed == 0 else "failed"
        job.message = (
            f"Full reload complete: cleared {deleted_metadata} metadata docs, "
            f"reset {reset_tickers} tickers, "
            f"{summary.tickers_processed} ok, {summary.tickers_failed} failed"
        )
        if summary.tickers_failed:
            job.error = f"Failed tickers: {', '.join(summary.failed_tickers)}"


job_manager = JobManager()
