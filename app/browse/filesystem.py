"""Filesystem listing for downloaded filing documents."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import FileSystemEntry, FilesystemBrowseResult

_TICKER_RE = re.compile(r"^[A-Z0-9.-]+$")


def normalize_ticker(ticker: str) -> str:
    """Return an upper-case ticker symbol or raise ``ValueError``."""

    normalized = ticker.strip().upper()
    if not normalized or not _TICKER_RE.fullmatch(normalized):
        raise ValueError(f"Invalid ticker: {ticker!r}")
    return normalized


def list_ticker_filesystem(ticker: str) -> FilesystemBrowseResult:
    """List on-disk filing files for ``ticker`` under ``EDGAR_DOWNLOAD_BASE``."""

    normalized = normalize_ticker(ticker)
    base = Path(settings.edgar_download_base)
    ticker_dir = base / normalized

    if not ticker_dir.is_dir():
        return FilesystemBrowseResult(
            base_path=str(base),
            ticker_path=str(ticker_dir),
            exists=False,
            accession_count=0,
            file_count=0,
            entries=[],
        )

    entries: list[FileSystemEntry] = []
    accession_count = 0
    file_count = 0

    for accession_dir in sorted(ticker_dir.iterdir()):
        if not accession_dir.is_dir():
            continue
        accession_count += 1
        for file_path in sorted(accession_dir.iterdir()):
            if not file_path.is_file():
                continue
            file_count += 1
            stat = file_path.stat()
            rel = file_path.relative_to(ticker_dir)
            entries.append(
                FileSystemEntry(
                    relative_path=str(rel),
                    accession_dir=accession_dir.name,
                    name=file_path.name,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                )
            )

    return FilesystemBrowseResult(
        base_path=str(base),
        ticker_path=str(ticker_dir),
        exists=True,
        accession_count=accession_count,
        file_count=file_count,
        entries=entries,
    )
