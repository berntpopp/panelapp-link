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


def build_host_allowlist(*base_urls: str) -> frozenset[str]:
    """Return the lowercased set of hosts parsed from the configured base URLs."""
    hosts: set[str] = set()
    for url in base_urls:
        host = urlsplit(url).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def make_url_guard(
    allowed_hosts: frozenset[str],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build an async httpx request event-hook enforcing ``allowed_hosts``.

    The raised messages are FIXED (they never interpolate the blocked URL/host),
    so a blocked request cannot smuggle caller- or upstream-influenced prose into
    a log or telemetry sink.
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError("Outbound request blocked: scheme is not https.")
        if url.username or url.password:
            raise DisallowedURLError("Outbound request blocked: userinfo is not permitted.")
        if (url.host or "").lower() not in allowed_hosts:
            raise DisallowedURLError("Outbound request blocked: host is not allowlisted.")

    return _guard
