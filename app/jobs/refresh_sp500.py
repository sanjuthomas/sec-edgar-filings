"""Refresh the S&P 500 constituent list from Wikipedia.

Intended to run once per weekday night before the filing scan job::

    python -m app.jobs.refresh_sp500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db.universe_store import universe_store
from app.jobs._stores import close_job_stores
from app.startup import initialize_runtime
from app.universe.sp500 import fetch_sp500_constituents

logger = logging.getLogger(__name__)


async def refresh_sp500_universe() -> int:
    await initialize_runtime()
    constituents = await fetch_sp500_constituents()
    result = await universe_store.refresh_sp500(constituents)
    logger.info(
        "Refreshed S&P 500 from %s: active=%d added=%d removed=%d",
        result.source,
        result.active_count,
        len(result.added),
        len(result.removed),
    )
    if result.added:
        logger.info("Added: %s", ", ".join(result.added))
    if result.removed:
        logger.info("Removed: %s", ", ".join(result.removed))
    print(result.model_dump_json(indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the S&P 500 constituent list into MongoDB."
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return asyncio.run(refresh_sp500_universe())
    finally:
        close_job_stores()


if __name__ == "__main__":
    sys.exit(main())
