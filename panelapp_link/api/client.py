"""Async HTTP client for the live PanelApp REST APIs.

Used by the ingest crawler (``panelapp_link.ingest``) to mirror both regions
(Genomics England UK and PanelApp Australia) into local SQLite. The base URL is
supplied per call so a single client can crawl both regions. PanelApp uses DRF
pagination (``count``/``next``/``results``); list endpoints follow ``next``
until exhausted. A concurrency cap plus jittered exponential backoff keeps us
polite to the upstream APIs.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

import httpx

from panelapp_link.exceptions import DownloadError, RateLimitError

if TYPE_CHECKING:
    from panelapp_link.config import PanelAppDataConfigModel

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


class PanelAppRestClient:
    """Minimal async client over the PanelApp ``/panels/`` REST API."""

    def __init__(
        self,
        config: PanelAppDataConfigModel,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Build a client; an injected ``client`` is used as-is (for tests)."""
        self._config = config
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": config.user_agent,
            },
        )

    async def _request(self, url: str) -> dict[str, Any]:
        """GET ``url`` with retries; raise the appropriate typed error on failure."""
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = DownloadError(f"PanelApp request to {url} failed: {exc}")
                last_status = None
            else:
                status = response.status_code
                if status in (403, 429):
                    raise RateLimitError(
                        f"PanelApp rate limit hit (HTTP {status}) for {url}.",
                        status_code=status,
                    )
                if status in _RETRYABLE_STATUS:
                    last_exc = DownloadError(
                        f"PanelApp returned {status} for {url}.", status_code=status
                    )
                    last_status = status
                elif status >= 400:
                    raise DownloadError(
                        f"PanelApp returned {status} for {url}.", status_code=status
                    )
                else:
                    return response.json()  # type: ignore[no-any-return]
            if attempt < self._config.max_retries:
                delay = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_MAX_SECONDS)
                await asyncio.sleep(random.uniform(0, delay))  # noqa: S311 - jitter only
        if last_exc is None:  # pragma: no cover - defensive; loop always sets last_exc
            last_exc = DownloadError(f"PanelApp request to {url} failed.", status_code=last_status)
        raise last_exc

    async def _list_paginated(self, url: str) -> list[dict[str, Any]]:
        """Follow DRF ``next`` links from ``url`` and return all ``results`` rows.

        A ``seen`` guard stops the rare case of a self-referential ``next`` link
        from looping forever.
        """
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        next_url: str | None = url
        while next_url and next_url not in seen:
            seen.add(next_url)
            payload = await self._request(next_url)
            page = payload.get("results")
            if isinstance(page, list):
                results.extend(page)
            next_url = payload.get("next")
        return results

    async def list_panels(self, base_url: str) -> list[dict[str, Any]]:
        """Return every panel summary across all pages for ``base_url``."""
        return await self._list_paginated(f"{base_url}/panels/")

    async def list_signed_off(self, base_url: str) -> list[dict[str, Any]]:
        """Return every signed-off panel row across all pages for ``base_url``."""
        return await self._list_paginated(f"{base_url}/panels/signedoff/")

    async def get_panel(self, base_url: str, panel_id: int) -> dict[str, Any]:
        """Return the full panel detail (genes/regions/strs) for ``panel_id``."""
        return await self._request(f"{base_url}/panels/{panel_id}/")

    async def aclose(self) -> None:
        """Close the underlying client if we own it."""
        if self._owns_client:
            await self._client.aclose()
