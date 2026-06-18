"""Tests for startup configuration logging."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from app.startup import initialize_runtime, log_startup_config


def test_log_startup_config_emits_connection_details(caplog):
    with caplog.at_level(logging.INFO, logger="app.startup"):
        log_startup_config()

    messages = "\n".join(record.message for record in caplog.records)
    assert "SEC EDGAR" in messages
    assert "MongoDB" in messages
    assert "Kafka" in messages
    assert "data.sec.gov" in messages


def test_initialize_runtime_skips_kafka_when_disabled():
    async def run():
        with (
            patch(
                "app.startup.settings",
                MagicMock(kafka_enabled=False),
            ),
            patch(
                "app.startup.filing_event_publisher.connect",
                AsyncMock(),
            ) as mock_connect,
        ):
            await initialize_runtime()
        mock_connect.assert_not_awaited()

    asyncio.run(run())
