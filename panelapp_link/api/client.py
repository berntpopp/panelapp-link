"""Async HTTP client for the live PanelApp REST APIs.

Used by the live :class:`~panelapp_link.services.panelapp_service.PanelAppService`
to answer queries against both regions (Genomics England UK and PanelApp
Australia) at request time. The base URL is supplied per call so a single client
can serve both regions. PanelApp uses DRF pagination (``count``/``next``/
``results``); list endpoints follow ``next`` until exhausted. A concurrency cap
plus jittered exponential backoff keeps us polite to the upstream APIs.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from panelapp_link.exceptions import DownloadError, RateLimitError

if TYPE_CHECKING:
    from panelapp_link.config import PanelAppDataConfigModel

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
# 429 is retried (it is the normal back-pressure signal); it gets a longer
# ceiling and honours ``Retry-After`` (PanelApp sends ``Retry-After: 60``). 403
# is treated as a hard denial and is never retried.
_RATE_LIMIT_MAX_SECONDS = 120.0


def _parse_retry_after(value: str | None) -> float | None:
    """Return the ``Retry-After`` delay in seconds, if it is a plain integer."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form is unsupported; fall back to backoff.
    return seconds if seconds >= 0 else None


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
            retry_after: float | None = None
            try:
                async with self._semaphore:
                    response = await self._client.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = DownloadError(f"PanelApp request to {url} failed: {exc}")
                last_status = None
            else:
                status = response.status_code
                if status == 403:
                    raise RateLimitError(
                        f"PanelApp denied the request (HTTP 403) for {url}.", status_code=403
                    )
                if status == 429:
                    last_exc = RateLimitError(
                        f"PanelApp rate-limited the crawl (HTTP 429) for {url}.", status_code=429
                    )
                    last_status = 429
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                elif status in _RETRYABLE_STATUS:
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
                await asyncio.sleep(self._retry_delay(attempt, last_status, retry_after))
        if last_exc is None:  # pragma: no cover - defensive; loop always sets last_exc
            last_exc = DownloadError(f"PanelApp request to {url} failed.", status_code=last_status)
        raise last_exc

    @staticmethod
    def _retry_delay(attempt: int, status: int | None, retry_after: float | None) -> float:
        """Jittered backoff; honour ``Retry-After`` and give 429 a longer ceiling."""
        if retry_after is not None:
            return min(retry_after, _RATE_LIMIT_MAX_SECONDS) + random.uniform(0, 1.0)  # noqa: S311
        cap = _RATE_LIMIT_MAX_SECONDS if status == 429 else _BACKOFF_MAX_SECONDS
        return random.uniform(0, min(_BACKOFF_BASE_SECONDS * (2**attempt), cap))  # noqa: S311

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

    async def get_genes_by_entity_name(
        self, base_url: str, entity_name: str
    ) -> list[dict[str, Any]]:
        """Return every ``/genes/?entity_name=`` result for ``entity_name``.

        Each result is an entity record that also carries a full ``panel``
        object, so this single call is the source for both ``get_gene_panels``
        and ``resolve_gene``. DRF ``next`` pages are followed to exhaustion.
        """
        return await self._list_paginated(f"{base_url}/genes/?entity_name={quote(entity_name)}")

    async def aclose(self) -> None:
        """Close the underlying client if we own it."""
        if self._owns_client:
            await self._client.aclose()
