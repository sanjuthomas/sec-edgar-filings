"""Publish filing-download events to Kafka."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import settings
from app.models import FilingMetadata

logger = logging.getLogger(__name__)


def _filing_downloaded_payload(metadata: FilingMetadata) -> dict[str, Any]:
    """Build the Kafka message body for one downloaded filing."""

    return {
        "event_type": "filing.downloaded",
        "schema_version": 1,
        **metadata.model_dump(mode="json"),
    }


class FilingEventPublisher:
    """Best-effort publisher for filing metadata after disk write."""

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None
        self._started = False

    @property
    def enabled(self) -> bool:
        return settings.kafka_enabled

    async def connect(self) -> None:
        """Start the Kafka producer when publishing is enabled."""

        producer = await self._ensure_producer()
        if producer is not None and self._started:
            logger.info(
                "Kafka producer connected: bootstrap_servers=%s topic=%s",
                settings.kafka_bootstrap_servers,
                settings.kafka_filing_downloaded_topic,
            )

    async def _ensure_producer(self) -> AIOKafkaProducer | None:
        if not self.enabled:
            return None

        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda value: json.dumps(value).encode("utf-8"),
                key_serializer=lambda key: key.encode("utf-8"),
            )

        if not self._started:
            await self._producer.start()
            self._started = True
        return self._producer

    async def publish_filing_downloaded(self, metadata: FilingMetadata) -> None:
        """Publish filing metadata when it is newly registered in MongoDB.

        Failures are logged and swallowed so a broker outage does not block
        Mongo metadata persistence (saga-style compensation is left to ops).
        """

        producer = await self._ensure_producer()
        if producer is None:
            return

        payload = _filing_downloaded_payload(metadata)
        topic = settings.kafka_filing_downloaded_topic
        try:
            record = await producer.send_and_wait(
                topic,
                value=payload,
                key=metadata.accession_number,
            )
            logger.info(
                "Kafka message published: topic=%s partition=%s offset=%s "
                "key=%s ticker=%s form=%s filing_date=%s local_path=%s",
                record.topic,
                record.partition,
                record.offset,
                metadata.accession_number,
                metadata.ticker,
                metadata.form,
                metadata.filing_date,
                metadata.local_path,
            )
        except KafkaError as exc:
            logger.warning(
                "Kafka publish failed for %r: %s",
                metadata.accession_number,
                exc,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected Kafka publish error for %r: %s",
                metadata.accession_number,
                exc,
            )

    async def aclose(self) -> None:
        if self._producer is not None and self._started:
            await self._producer.stop()
        self._producer = None
        self._started = False

    def close(self) -> None:
        """Release the producer when no event loop is running."""

        if self._producer is None or not self._started:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
        else:
            logger.warning(
                "Kafka publisher close skipped while event loop is running; "
                "call aclose() from async shutdown instead"
            )


filing_event_publisher = FilingEventPublisher()
