"""MongoDB-backed store for index universes such as the S&P 500.

Each constituent is stored as its own document so nightly refreshes can mark
additions and removals while keeping history.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pymongo.errors import PyMongoError

from app.config import settings
from app.models import Sp500Constituent, Sp500TickerStatus, UniverseRefreshResult

logger = logging.getLogger(__name__)


class UniverseStore:
    """Async accessor for tracked index universes."""

    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None

    def _collection(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            )
        db = self._client[settings.mongo_db]
        return db[settings.mongo_sp500_collection]

    async def get_active_sp500_tickers(self) -> list[str]:
        """Return active S&P 500 tickers sorted alphabetically."""

        try:
            cursor = self._collection().find(
                {"active": True},
                projection={"_id": 1},
            ).sort("_id", 1)
            docs = await cursor.to_list(length=None)
        except PyMongoError as exc:
            logger.warning("Mongo S&P 500 lookup failed: %s", exc)
            return []
        return [doc["_id"] for doc in docs]

    async def get_last_download_at(self, ticker: str) -> datetime | None:
        """Return when ``ticker`` was last downloaded successfully, if ever."""

        try:
            doc = await self._collection().find_one(
                {"_id": ticker.upper()},
                projection={"last_download_at": 1, "last_download_status": 1},
            )
        except PyMongoError as exc:
            logger.warning(
                "Mongo S&P 500 download lookup failed for %r: %s", ticker, exc
            )
            return None
        if not doc or doc.get("last_download_status") != "ok":
            return None
        return doc.get("last_download_at")

    async def get_last_scan_at(self, ticker: str) -> datetime | None:
        """Return when ``ticker`` was last scanned successfully, if ever."""

        return await self.get_last_download_at(ticker)

    async def record_download(
        self,
        ticker: str,
        *,
        status: str,
        lookback_days: int,
        filings_found: int = 0,
        filings_downloaded: int = 0,
        filings_skipped: int = 0,
        error: str | None = None,
    ) -> None:
        """Persist the outcome of downloading filings for one S&P 500 ticker."""

        now = datetime.now(UTC)
        payload = {
            "last_download_at": now,
            "last_download_status": status,
            "last_download_lookback_days": lookback_days,
            "last_download_filings_found": filings_found,
            "last_download_filings_downloaded": filings_downloaded,
            "last_download_filings_skipped": filings_skipped,
            "last_download_error": error,
        }
        try:
            await self._collection().update_one(
                {"_id": ticker.upper()},
                {"$set": payload},
                upsert=False,
            )
        except PyMongoError as exc:
            logger.warning(
                "Mongo S&P 500 download record failed for %r: %s", ticker, exc
            )

    async def record_scan(
        self,
        ticker: str,
        *,
        status: str,
        lookback_days: int,
        filings_scanned: int = 0,
        new_authorization_count: int = 0,
        reference_count: int = 0,
        error: str | None = None,
    ) -> None:
        """Persist the outcome of scanning one S&P 500 ticker."""

        now = datetime.now(UTC)
        payload = {
            "last_scan_at": now,
            "last_scan_status": status,
            "last_scan_lookback_days": lookback_days,
            "last_scan_filings_scanned": filings_scanned,
            "last_scan_new_authorizations": new_authorization_count,
            "last_scan_references": reference_count,
            "last_scan_error": error,
        }
        try:
            await self._collection().update_one(
                {"_id": ticker.upper()},
                {"$set": payload},
                upsert=False,
            )
        except PyMongoError as exc:
            logger.warning("Mongo S&P 500 scan record failed for %r: %s", ticker, exc)

    async def reset_download_status(self) -> int:
        """Clear per-ticker download fields so the next batch treats all as fresh."""

        try:
            result = await self._collection().update_many(
                {},
                {
                    "$unset": {
                        "last_download_at": "",
                        "last_download_status": "",
                        "last_download_lookback_days": "",
                        "last_download_filings_found": "",
                        "last_download_filings_downloaded": "",
                        "last_download_filings_skipped": "",
                        "last_download_error": "",
                    }
                },
            )
            return result.modified_count
        except PyMongoError as exc:
            logger.warning("Mongo S&P 500 download reset failed: %s", exc)
            return 0

    async def list_ticker_statuses(self, *, active_only: bool = True) -> list[Sp500TickerStatus]:
        """Return download state for each constituent."""

        query = {"active": True} if active_only else {}
        try:
            cursor = self._collection().find(query).sort("_id", 1)
            docs = await cursor.to_list(length=None)
        except PyMongoError as exc:
            logger.warning("Mongo S&P 500 status list failed: %s", exc)
            return []

        statuses: list[Sp500TickerStatus] = []
        for doc in docs:
            statuses.append(
                Sp500TickerStatus(
                    ticker=doc["_id"],
                    company_name=doc.get("company_name"),
                    active=bool(doc.get("active", True)),
                    last_download_at=doc.get("last_download_at"),
                    last_download_status=doc.get("last_download_status"),
                    last_download_lookback_days=doc.get("last_download_lookback_days"),
                    last_download_filings_found=doc.get("last_download_filings_found"),
                    last_download_filings_downloaded=doc.get(
                        "last_download_filings_downloaded"
                    ),
                    last_download_filings_skipped=doc.get(
                        "last_download_filings_skipped"
                    ),
                    last_download_error=doc.get("last_download_error"),
                )
            )
        return statuses

    async def refresh_sp500(
        self,
        constituents: list[Sp500Constituent],
        *,
        source: str = "wikipedia",
        source_url: str = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    ) -> UniverseRefreshResult:
        """Upsert the latest constituents and mark additions/removals."""

        now = datetime.now(UTC)
        current = {item.ticker: item for item in constituents}
        previous_active = set(await self.get_active_sp500_tickers())

        operations: list[UpdateOne] = []
        for ticker, item in sorted(current.items()):
            operations.append(
                UpdateOne(
                    {"_id": ticker},
                    {
                        "$set": {
                            "company_name": item.company_name,
                            "active": True,
                            "last_seen_at": now,
                            "source": source,
                            "source_url": source_url,
                            "removed_at": None,
                        },
                        "$setOnInsert": {"first_seen_at": now},
                    },
                    upsert=True,
                )
            )

        removed = sorted(previous_active - current.keys())
        for ticker in removed:
            operations.append(
                UpdateOne(
                    {"_id": ticker},
                    {
                        "$set": {
                            "active": False,
                            "removed_at": now,
                            "last_seen_at": now,
                        }
                    },
                )
            )

        added = sorted(current.keys() - previous_active)
        try:
            if operations:
                await self._collection().bulk_write(operations, ordered=False)
        except PyMongoError as exc:
            logger.warning("Mongo S&P 500 refresh failed: %s", exc)
            raise

        return UniverseRefreshResult(
            source=source,
            source_url=source_url,
            refreshed_at=now,
            active_count=len(current),
            added=added,
            removed=removed,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


universe_store = UniverseStore()
