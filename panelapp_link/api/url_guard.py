"""Outbound-URL guard for the live PanelApp REST client (F-17).

An httpx *request* event-hook that fires on every hop -- including redirects that
httpx auto-follows -- and validates each outgoing URL against an exact host
allowlist derived from the configured PanelApp base URLs. A violation raises
:class:`~panelapp_link.exceptions.DisallowedURLError`, which is non-retryable.

The allowlist is DERIVED from config (never hardcoded) so an operator override of
``uk_api_url``/``au_api_url`` keeps working; the match is exact-host (no suffix or
substring matching, so ``evil-panelapp-aus.org`` is not accepted).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from panelapp_link.exceptions import DisallowedURLError

HTTP_POLICY_ERROR = "Outbound HTTP policy rejected the request."
Origin = tuple[str, int]


def build_origin_allowlist(*base_urls: str) -> frozenset[Origin]:
    """Return normalized ``(hostname, effective_port)`` configured origins."""
    origins: set[Origin] = set()
    for url in base_urls:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host:
            try:
                port = parsed.port
            except ValueError:
                continue
            origins.add((host.lower(), 443 if port is None else port))
    return frozenset(origins)


def make_url_guard(
    allowed_origins: frozenset[Origin],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build an async httpx request event-hook enforcing ``allowed_hosts``.

    The raised messages are FIXED (they never interpolate the blocked URL/host),
    so a blocked request cannot smuggle caller- or upstream-influenced prose into
    a log or telemetry sink.
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(HTTP_POLICY_ERROR)
        if url.userinfo:
            raise DisallowedURLError(HTTP_POLICY_ERROR)
        try:
            port = url.port
        except ValueError:
            raise DisallowedURLError(HTTP_POLICY_ERROR) from None
        origin = ((url.host or "").lower(), 443 if port is None else port)
        if origin not in allowed_origins:
            raise DisallowedURLError(HTTP_POLICY_ERROR)

    return _guard
