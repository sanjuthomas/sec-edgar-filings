"""HTTP client for SEC EDGAR.

Wraps ``httpx`` with the SEC's required ``User-Agent`` header, a simple
client-side rate limiter, and retry/backoff for transient failures.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.config import settings


class EdgarError(RuntimeError):
    """Raised when EDGAR cannot satisfy a request."""


class _RateLimiter:
    """Minimal async rate limiter enforcing a minimum interval between calls."""

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class EdgarClient:
    """Async EDGAR client. Use as an async context manager."""

    def __init__(self) -> None:
        self._limiter = _RateLimiter(settings.max_requests_per_second)
        self._client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
        )

    async def __aenter__(self) -> "EdgarClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(settings.max_retries + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.get(url)
            except httpx.RequestError as exc:  # network / timeout
                last_exc = exc
            else:
                if response.status_code == 200:
                    return response
                # Retry on rate limiting and transient server errors.
                if response.status_code in (429, 500, 502, 503, 504):
                    last_exc = EdgarError(
                        f"GET {url} -> HTTP {response.status_code}"
                    )
                else:
                    raise EdgarError(f"GET {url} -> HTTP {response.status_code}")
            # Exponential backoff before the next attempt.
            if attempt < settings.max_retries:
                await asyncio.sleep(2 ** attempt)
        raise EdgarError(f"GET {url} failed after retries: {last_exc}")

    async def get_json(self, url: str) -> dict:
        response = await self._get(url)
        return response.json()

    async def get_text(self, url: str) -> str:
        response = await self._get(url)
        return response.text

    async def get_bytes(self, url: str) -> bytes:
        response = await self._get(url)
        return response.content
