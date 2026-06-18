"""Job control API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.jobs.manager import JobConflictError, job_manager
from app.models import JobProgress

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class SingleTickerRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    lookback_days: int = Field(..., ge=1, le=3650)


class BatchDownloadRequest(BaseModel):
    lookback_days: int = Field(..., ge=1, le=3650)
    skip_refresh: bool = False


class FullReloadRequest(BaseModel):
    lookback_days: int = Field(..., ge=1, le=3650)
    skip_refresh: bool = False


def _conflict_response(exc: JobConflictError) -> HTTPException:
    current = job_manager.get_current_job()
    detail = {"message": str(exc)}
    if current is not None:
        detail["current_job"] = current.model_dump(mode="json")
    return HTTPException(status_code=409, detail=detail)


@router.get("", response_model=list[JobProgress])
async def list_jobs() -> list[JobProgress]:
    return job_manager.list_jobs()


@router.get("/current", response_model=JobProgress | None)
async def get_current_job() -> JobProgress | None:
    return job_manager.get_current_job()


@router.get("/{job_id}", response_model=JobProgress)
async def get_job(job_id: str) -> JobProgress:
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job


@router.post("/download/ticker", response_model=JobProgress, status_code=202)
async def start_single_ticker_download(body: SingleTickerRequest) -> JobProgress:
    try:
        return await job_manager.start_single_ticker(
            body.ticker,
            lookback_days=body.lookback_days,
        )
    except JobConflictError as exc:
        raise _conflict_response(exc) from exc


@router.post("/download/batch", response_model=JobProgress, status_code=202)
async def start_batch_download(body: BatchDownloadRequest) -> JobProgress:
    try:
        return await job_manager.start_batch_download(
            lookback_days=body.lookback_days,
            skip_refresh=body.skip_refresh,
        )
    except JobConflictError as exc:
        raise _conflict_response(exc) from exc


@router.post("/download/full-reload", response_model=JobProgress, status_code=202)
async def start_full_reload(body: FullReloadRequest) -> JobProgress:
    try:
        return await job_manager.start_full_reload(
            lookback_days=body.lookback_days,
            skip_refresh=body.skip_refresh,
        )
    except JobConflictError as exc:
        raise _conflict_response(exc) from exc
