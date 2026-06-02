"""MongoDB-backed cache for ticker -> CIK lookups.

The SEC publishes a ticker->CIK map that the resolver would otherwise fetch on
every request. Caching it in MongoDB lets repeated lookups (across requests and
process restarts) avoid the network round-trip to EDGAR.

The store degrades gracefully: if MongoDB is unreachable, reads return ``None``
and writes are no-ops, so the resolver simply falls back to a direct EDGAR
call. The application keeps working without Mongo, just without the cache.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pymongo.errors import PyMongoError

from app.config import settings

logger = logging.getLogger(__name__)


class TickerStore:
    """Async accessor for the cached ticker->CIK collection.

    Documents are shaped as::

        {"_id": "ADBE", "cik": "0000796343", "company_name": "ADOBE INC."}

    where ``_id`` is the upper-cased ticker (using the SEC's dash notation for
    class shares, e.g. ``BRK-B``).
    """

    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None

    def _collection(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            )
        db = self._client[settings.mongo_db]
        return db[settings.mongo_tickers_collection]

    async def get(self, ticker: str) -> tuple[str, str] | None:
        """Return ``(cik, company_name)`` for ``ticker`` from the cache.

        Returns ``None`` on a cache miss or if Mongo is unavailable.
        """

        try:
            doc = await self._collection().find_one({"_id": ticker})
        except PyMongoError as exc:
            logger.warning("Mongo lookup failed for %r: %s", ticker, exc)
            return None
        if not doc:
            return None
        return doc["cik"], doc.get("company_name", "")

    async def put_many(self, mapping: dict[str, tuple[str, str]]) -> None:
        """Bulk-upsert the full ticker map so future lookups hit the cache."""

        if not mapping:
            return
        operations = [
            UpdateOne(
                {"_id": ticker},
                {"$set": {"cik": cik, "company_name": name}},
                upsert=True,
            )
            for ticker, (cik, name) in mapping.items()
        ]
        try:
            await self._collection().bulk_write(operations, ordered=False)
        except PyMongoError as exc:
            logger.warning("Mongo bulk upsert failed: %s", exc)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


# Process-wide singleton so the Mongo connection pool is reused across requests.
ticker_store = TickerStore()
