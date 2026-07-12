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
import json
import random
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from panelapp_link.api.url_guard import HTTP_POLICY_ERROR, build_origin_allowlist, make_url_guard
from panelapp_link.exceptions import (
    DisallowedURLError,
    DownloadError,
    RateLimitError,
    ResponseTooLargeError,
)

if TYPE_CHECKING:
    from panelapp_link.config import PanelAppDataConfigModel

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
# 429 is retried (it is the normal back-pressure signal); it gets a longer
# ceiling and honours ``Retry-After`` (PanelApp sends ``Retry-After: 60``). 403
# is treated as a hard denial and is never retried.
_RATE_LIMIT_MAX_SECONDS = 120.0

# F-17 resource ceilings. All three fail CLOSED (raise ``DownloadError``) rather
# than truncate: search filters the full panel list, so a silently short list
# would drop valid panels. ``_MAX_REDIRECTS`` bounds redirect hops (each hop is
# still host-validated by the event-hook guard).
_MAX_REDIRECTS = 5
_MAX_PAGES = 100
_MAX_ROWS = 100_000
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB per response.


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
        # Allowlist is DERIVED from the configured base URLs (never hardcoded), so
        # an operator override of either region URL keeps working. Redirects stay
        # enabled but every hop is validated by the request event-hook.
        allowed_origins = build_origin_allowlist(config.uk_api_url, config.au_api_url)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout),
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            event_hooks={"request": [make_url_guard(allowed_origins)]},
            headers={
                "Accept": "application/json",
                "User-Agent": config.user_agent,
            },
        )

    async def _request(self, url: str) -> dict[str, Any]:
        """GET ``url`` with retries; raise the appropriate typed error on failure.

        Error messages are FIXED and status-keyed: neither the request ``url``
        (which embeds caller-influenced query text) nor the transport ``str(exc)``
        nor any upstream response body is interpolated into the raised exception.
        The HTTP status is a bounded, non-attacker-controlled scalar, so it is the
        only request-specific detail kept. This keeps caller-influenced prose out
        of the exception (and therefore out of any log/telemetry sink).
        """
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(self._config.max_retries + 1):
            retry_after: float | None = None
            try:
                # Stream the body so the byte ceiling fails CLOSED before decode:
                # a buffered ``.json()`` would materialise an oversized body first.
                # A disallowed redirect hop raises ``DisallowedURLError`` from the
                # event hook here; it is not an httpx transport error, so it is not
                # caught below and propagates immediately (non-retryable).
                async with self._semaphore, self._client.stream("GET", url) as response:
                    status = response.status_code
                    if status == 403:
                        raise RateLimitError(
                            "PanelApp denied the request (HTTP 403).", status_code=403
                        )
                    if status == 429:
                        last_exc = RateLimitError(
                            "PanelApp rate-limited the request (HTTP 429).", status_code=429
                        )
                        last_status = 429
                        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    elif status in _RETRYABLE_STATUS:
                        last_exc = DownloadError(
                            f"PanelApp returned HTTP {status}.", status_code=status
                        )
                        last_status = status
                    elif status >= 400:
                        raise DownloadError(f"PanelApp returned HTTP {status}.", status_code=status)
                    else:
                        body = await self._read_capped(response)
                        return json.loads(body)  # type: ignore[no-any-return]
            except httpx.TooManyRedirects:
                raise DisallowedURLError(HTTP_POLICY_ERROR) from None
            except (httpx.TimeoutException, httpx.TransportError):
                last_exc = DownloadError("PanelApp request failed (network error).")
                last_status = None
            if attempt < self._config.max_retries:
                await asyncio.sleep(self._retry_delay(attempt, last_status, retry_after))
        if last_exc is None:  # pragma: no cover - defensive; loop always sets last_exc
            last_exc = DownloadError("PanelApp request failed.", status_code=last_status)
        raise last_exc

    @staticmethod
    async def _read_capped(response: httpx.Response) -> bytes:
        """Read the streamed body, aborting past ``_MAX_RESPONSE_BYTES``.

        Fails CLOSED (raise ``DownloadError``) rather than truncating: a partial
        JSON body is unparseable, and a silently short list would drop panels.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(HTTP_POLICY_ERROR)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _retry_delay(attempt: int, status: int | None, retry_after: float | None) -> float:
        """Jittered backoff; honour ``Retry-After`` and give 429 a longer ceiling."""
        if retry_after is not None:
            return min(retry_after, _RATE_LIMIT_MAX_SECONDS) + random.uniform(0, 1.0)  # noqa: S311
        cap = _RATE_LIMIT_MAX_SECONDS if status == 429 else _BACKOFF_MAX_SECONDS
        return random.uniform(0, min(_BACKOFF_BASE_SECONDS * (2**attempt), cap))  # noqa: S311

    async def _list_paginated(self, url: str) -> list[dict[str, Any]]:
        """Follow DRF ``next`` links from ``url`` and return all ``results`` rows.

        The upstream ``next`` value is untrusted JSON (not an httpx redirect, so it
        bypasses the client's event-hook guard) and is validated here per hop:
        it must share the initiating request's FULL ORIGIN (scheme, host, AND
        port) or fail closed. Every ceiling -- pages, rows, and a revisited
        (cyclic) ``next`` -- also fails CLOSED (raises ``DownloadError``); none
        truncates, because ``search`` filters the full list downstream, so a
        silently short list would drop valid panels.
        """
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        base_origin = self._origin(url)
        next_url: str | None = url
        pages = 0
        while next_url is not None:
            if next_url in seen:
                # A same-origin ``next`` pointing back at an already-fetched page
                # would otherwise stop the loop early and return a partial list;
                # fail loud instead of silently truncating.
                raise DownloadError("PanelApp pagination revisited a page.")
            seen.add(next_url)
            pages += 1
            if pages > _MAX_PAGES:
                raise DownloadError("PanelApp pagination exceeded the page ceiling.")
            payload = await self._request(next_url)
            page = payload.get("results")
            if isinstance(page, list):
                results.extend(page)
                if len(results) > _MAX_ROWS:
                    raise DownloadError("PanelApp pagination exceeded the row ceiling.")
            next_url = self._safe_next_url(payload.get("next"), base_origin)
        return results

    @staticmethod
    def _origin(raw_url: str) -> tuple[str, int]:
        """Return the ``(host, port)`` origin of ``raw_url`` under https normalization.

        The scheme is normalized to https before the port default is applied, so a
        reverse proxy's http ``next`` for an https listing shares the origin of its
        https base (both resolve to the default 443) while any *explicit* alternate
        port (``:8080``) resolves to a distinct origin. A malformed port fails
        CLOSED.
        """
        parts = urlsplit(raw_url)
        try:
            port = parts.port
        except ValueError:
            raise DownloadError("PanelApp pagination link has an invalid port.") from None
        return (parts.hostname or "").lower(), 443 if port is None else port

    @staticmethod
    def _safe_next_url(raw_next: Any, base_origin: tuple[str, int]) -> str | None:
        """Validate a DRF ``next`` link, or return ``None`` when there is no next.

        The link must share the initiating request's FULL ORIGIN -- scheme, host,
        AND port -- or it fails CLOSED. Host-only matching is not enough: an
        alternate port (``...:8080``) or a different scheme/host is a distinct
        origin and must be rejected (the event-hook guard checks neither port nor
        this untrusted link). Embedded userinfo and non-http(s) schemes also fail
        closed. The scheme is NORMALIZED to https for the follow-up request -- a
        reverse proxy may legitimately emit an http ``next`` for an https listing
        -- but ONLY after the host+port have been confirmed to match the base
        origin.
        """
        if raw_next is None:
            return None
        if not isinstance(raw_next, str):
            raise DownloadError("PanelApp returned a malformed pagination link.")
        parts = urlsplit(raw_next)
        if parts.scheme not in ("http", "https"):
            raise DownloadError("PanelApp pagination link uses an unexpected scheme.")
        # ANY userinfo delimiter is rejected -- including the empty ``:@`` form,
        # which leaves username/password both "" yet still smuggles a ``@`` into
        # the netloc (a hostname never legitimately contains ``@``).
        if "@" in parts.netloc:
            raise DownloadError("PanelApp pagination link carries userinfo.")
        if PanelAppRestClient._origin(raw_next) != base_origin:
            raise DownloadError("PanelApp pagination link changed origin.")
        return urlunsplit(parts._replace(scheme="https"))

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
