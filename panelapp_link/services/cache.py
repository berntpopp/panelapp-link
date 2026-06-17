"""Request cache + single-flight coalescing for the live PanelApp service.

Two cooperating pieces, kept out of ``panelapp_service`` so that module stays in
its line budget and the caching policy is independently testable:

- :class:`TTLCache` -- a tiny insertion-ordered TTL cache (disabled when
  ``maxsize <= 0``).
- :class:`RequestCache` -- wraps the TTL cache with **single-flight** coalescing:
  concurrent identical fetches share one in-flight call instead of stampeding the
  rate-limited PanelApp API, and each fill is timed + recorded into the
  per-request telemetry scope and the process-wide RED metrics. The cold
  double-fetch (``/panels/`` + ``/panels/signedoff/`` for both regions) is paid
  at most once per key per TTL window, even under a burst of identical lookups.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from panelapp_link.observability import telemetry, tracing
from panelapp_link.observability.metrics import get_metrics


class TTLCache:
    """Tiny insertion-ordered TTL cache (disabled when ``maxsize <= 0``)."""

    def __init__(self, maxsize: int, ttl: int) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        if self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: str, value: Any) -> None:
        if self._maxsize <= 0:
            return
        if len(self._store) >= self._maxsize and key not in self._store:
            self._store.pop(next(iter(self._store)), None)
        self._store[key] = (time.monotonic() + self._ttl, value)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._store), "maxsize": self._maxsize, "ttl": self._ttl}


class RequestCache:
    """TTL cache + single-flight coalescing with telemetry/metrics on every fill."""

    def __init__(self, *, maxsize: int, ttl: int) -> None:
        self._cache = TTLCache(maxsize, ttl)
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    def get(self, key: str) -> Any | None:
        """Peek the underlying TTL cache without recording a hit/miss."""
        return self._cache.get(key)

    def put(self, key: str, value: Any) -> None:
        """Overwrite a cache entry (used by background refresh)."""
        self._cache.put(key, value)

    def stats(self) -> dict[str, int]:
        return self._cache.stats()

    async def get_or_fetch(
        self,
        key: str,
        region: str,
        endpoint: str,
        fetch: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return ``key`` from cache, or fetch it once (coalescing concurrent calls)."""
        cached = self._cache.get(key)
        if cached is not None:
            telemetry.record_cache_hit()
            get_metrics().record_cache("hit")
            return cached

        inflight = self._inflight.get(key)
        if inflight is not None:
            telemetry.record_coalesced()
            get_metrics().record_cache("coalesced")
            return await inflight

        task: asyncio.Task[Any] = asyncio.ensure_future(
            self._fetch_and_cache(key, region, endpoint, fetch)
        )
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

    async def _fetch_and_cache(
        self,
        key: str,
        region: str,
        endpoint: str,
        fetch: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Time the upstream fetch, cache it, and record the miss (one trace span)."""
        request_id = telemetry.current_request_id()
        start = time.perf_counter()
        with tracing.upstream_span(region, endpoint, request_id):
            data = await fetch()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        telemetry.record_upstream(region, endpoint, elapsed_ms)
        get_metrics().record_upstream(region, elapsed_ms)
        self._cache.put(key, data)
        telemetry.record_cache_miss()
        get_metrics().record_cache("miss")
        return data
