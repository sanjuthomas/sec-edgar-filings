"""Tests for job control API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models import DownloadTickerResult, JobProgress


def test_get_current_job_when_idle():
    client = TestClient(app)
    with patch(
        "app.api.jobs.job_manager.get_current_job",
        return_value=None,
    ):
        response = client.get("/api/jobs/current")
    assert response.status_code == 200
    assert response.json() is None


def test_start_single_ticker_returns_202():
    client = TestClient(app)
    job = JobProgress(
        job_id="abc123",
        job_type="single_ticker",
        status="pending",
        ticker="AAPL",
        lookback_days=30,
        tickers_total=1,
    )
    with patch(
        "app.api.jobs.job_manager.start_single_ticker",
        AsyncMock(return_value=job),
    ):
        response = client.post(
            "/api/jobs/download/ticker",
            json={"ticker": "AAPL", "lookback_days": 30},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "abc123"
    assert body["ticker"] == "AAPL"


def test_start_batch_download_conflict_returns_409():
    client = TestClient(app)
    current = JobProgress(
        job_id="running1",
        job_type="batch_download",
        status="running",
        lookback_days=365,
        current_ticker="MSFT",
        tickers_total=500,
        tickers_completed=10,
    )
    with patch(
        "app.api.jobs.job_manager.start_batch_download",
        AsyncMock(side_effect=__import__("app.jobs.manager", fromlist=["JobConflictError"]).JobConflictError("busy")),
    ), patch(
        "app.api.jobs.job_manager.get_current_job",
        return_value=current,
    ):
        response = client.post(
            "/api/jobs/download/batch",
            json={"lookback_days": 365, "skip_refresh": False},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["current_job"]["job_id"] == "running1"


def test_get_runtime_config():
    client = TestClient(app)
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert "kafka_enabled" in body
    assert body["kafka_filing_downloaded_topic"] == "filings"


def test_get_sp500_status_empty():
    client = TestClient(app)
    with patch(
        "app.api.universe.universe_store.list_ticker_statuses",
        AsyncMock(return_value=[]),
    ):
        response = client.get("/api/universe/sp500/status")

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 0
    assert body["never_downloaded"] == 0


def test_manager_runs_single_ticker_job():
    from app.jobs.manager import JobManager

    async def run():
        manager = JobManager()
        result = DownloadTickerResult(
            ticker="GS",
            cik="0000886982",
            company_name="GOLDMAN SACHS GROUP INC",
            lookback_days=30,
            filings_found=2,
            filings_downloaded=1,
            filings_skipped=1,
        )
        with patch(
            "app.jobs.manager.run_single_ticker_download",
            AsyncMock(return_value=result),
        ):
            job = await manager.start_single_ticker("GS", lookback_days=30)
            assert manager._task is not None
            await manager._task

        final = manager.get_job(job.job_id)
        assert final is not None
        assert final.status == "completed"
        assert final.total_filings_downloaded == 1
        return final

    import asyncio

    final = asyncio.run(run())
    assert final.ticker == "GS"
