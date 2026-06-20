"""MongoDB store for downloaded filing metadata."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.config import settings
from app.models import FilingMetadata

logger = logging.getLogger(__name__)


class FilingStore:
    """Async accessor for filing metadata keyed by accession number."""

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
        return db[settings.mongo_filing_metadata_collection]

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        try:
            collection = self._collection()
            await collection.create_index("ticker")
            await collection.create_index("filing_date")
            self._indexes_ready = True
        except PyMongoError as exc:
            logger.warning("Mongo index creation failed: %s", exc)

    async def exists(self, accession_number: str) -> bool:
        """Return whether a filing accession is already stored."""

        await self._ensure_indexes()
        try:
            doc = await self._collection().find_one(
                {"_id": accession_number},
                projection={"_id": 1},
            )
        except PyMongoError as exc:
            logger.warning(
                "Mongo filing lookup failed for %r: %s", accession_number, exc
            )
            return False
        return doc is not None

    async def get_by_ticker(self, ticker: str) -> list[FilingMetadata]:
        """Return all stored filings for ``ticker``, newest first."""

        await self._ensure_indexes()
        try:
            cursor = (
                self._collection()
                .find({"ticker": ticker.strip().upper()})
                .sort("filing_date", -1)
            )
            docs = await cursor.to_list(length=None)
        except PyMongoError as exc:
            logger.warning("Mongo filing list failed for %r: %s", ticker, exc)
            return []

        results: list[FilingMetadata] = []
        for doc in docs:
            doc.pop("_id", None)
            try:
                results.append(FilingMetadata.model_validate(doc))
            except ValidationError as exc:
                logger.warning("Invalid filing metadata for %r: %s", ticker, exc)
        return results

    async def put(self, metadata: FilingMetadata) -> None:
        """Persist metadata for one filing."""

        await self._ensure_indexes()
        payload = metadata.model_dump(mode="json")
        payload["_id"] = metadata.accession_number
        if payload.get("downloaded_at") is None:
            payload["downloaded_at"] = datetime.now(UTC).isoformat()
        try:
            await self._collection().replace_one(
                {"_id": metadata.accession_number},
                payload,
                upsert=True,
            )
        except PyMongoError as exc:
            logger.warning(
                "Mongo filing upsert failed for %r: %s",
                metadata.accession_number,
                exc,
            )

    async def count_all(self) -> int:
        """Return the number of stored filing metadata documents."""

        await self._ensure_indexes()
        try:
            return await self._collection().count_documents({})
        except PyMongoError as exc:
            logger.warning("Mongo filing count failed: %s", exc)
            return 0

    async def clear_all(self) -> int:
        """Delete all filing metadata. Returns the number of documents removed."""

        await self._ensure_indexes()
        try:
            result = await self._collection().delete_many({})
            return result.deleted_count
        except PyMongoError as exc:
            logger.warning("Mongo filing clear failed: %s", exc)
            return 0

    async def delete_by_ticker(self, ticker: str) -> int:
        """Delete all filing metadata for ``ticker``. Returns documents removed."""

        normalized = ticker.strip().upper()
        await self._ensure_indexes()
        try:
            result = await self._collection().delete_many({"ticker": normalized})
            return result.deleted_count
        except PyMongoError as exc:
            logger.warning(
                "Mongo filing delete failed for %r: %s", normalized, exc
            )
            return 0

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._indexes_ready = False


filing_store = FilingStore()
