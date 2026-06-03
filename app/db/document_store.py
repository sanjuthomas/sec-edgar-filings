"""MongoDB-backed cache for processed EDGAR document scans.

Each narrative filing document has a stable SEC archives URL. Once scanned,
the extracted buyback announcements (including an empty list) are persisted in
the ``filings`` collection so later requests skip downloading and re-parsing
the same HTML.

The store degrades gracefully: if MongoDB is unreachable, reads return ``None``
and writes are no-ops, so scanning falls back to direct EDGAR calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.config import settings
from app.models import BuybackAnnouncement

logger = logging.getLogger(__name__)


class DocumentStore:
    """Async accessor for cached document scan results.

    Documents are shaped as::

        {
            "_id": "https://www.sec.gov/Archives/edgar/data/.../ex99.htm",
            "ticker": "ADBE",
            "cik": "0000796343",
            "announcements": [ {...}, ... ],
            "processed_at": ISODate("..."),
        }

    where ``_id`` is the full document URL and ``announcements`` may be empty.
    """

    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None
        self._indexes_ready = False

    def _collection(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            )
        db = self._client[settings.mongo_db]
        return db[settings.mongo_filings_collection]

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        try:
            collection = self._collection()
            await collection.create_index("ticker")
            await collection.create_index("cik")
            self._indexes_ready = True
        except PyMongoError as exc:
            logger.warning("Mongo index creation failed: %s", exc)

    async def get(self, document_url: str) -> list[BuybackAnnouncement] | None:
        """Return cached announcements for ``document_url``.

        Returns ``None`` on a cache miss or if Mongo is unavailable.
        """

        await self._ensure_indexes()
        try:
            doc = await self._collection().find_one({"_id": document_url})
        except PyMongoError as exc:
            logger.warning("Mongo document lookup failed for %r: %s", document_url, exc)
            return None
        if not doc:
            return None
        raw = doc.get("announcements")
        if raw is None:
            return None
        try:
            return [BuybackAnnouncement.model_validate(item) for item in raw]
        except ValidationError as exc:
            logger.warning(
                "Mongo document cache invalid for %r: %s", document_url, exc
            )
            return None

    async def put(
        self,
        document_url: str,
        announcements: list[BuybackAnnouncement],
        *,
        ticker: str,
        cik: str,
    ) -> None:
        """Persist scan results for ``document_url`` (including no matches)."""

        await self._ensure_indexes()
        payload = {
            "_id": document_url,
            "ticker": ticker.strip().upper(),
            "cik": cik,
            "announcements": [
                ann.model_dump(mode="json") for ann in announcements
            ],
            "processed_at": datetime.now(UTC),
        }
        try:
            await self._collection().replace_one(
                {"_id": document_url},
                payload,
                upsert=True,
            )
        except PyMongoError as exc:
            logger.warning("Mongo document upsert failed for %r: %s", document_url, exc)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._indexes_ready = False


document_store = DocumentStore()
