"""Unit tests for the outbound-URL event-hook guard (F-17).

The guard fires on every httpx request hop (including auto-followed redirects) and
fails closed on a non-https scheme, embedded userinfo, or a host outside the
allowlist derived from the configured PanelApp base URLs.
"""

from __future__ import annotations

import httpx
import pytest

from panelapp_link.api.url_guard import HTTP_POLICY_ERROR, build_origin_allowlist, make_url_guard
from panelapp_link.exceptions import DisallowedURLError, ResponseTooLargeError

_ALLOWED = frozenset({("panelapp.genomicsengland.co.uk", 443), ("panelapp-aus.org", 443)})


def test_build_origin_allowlist_lowercases_and_normalizes_effective_ports() -> None:
    """Origins derive from configuration, with :443 equivalent to an omitted port."""
    origins = build_origin_allowlist(
        "https://PanelApp.GenomicsEngland.co.uk/api/v1",
        "https://panelapp-aus.org/api/v1",
        "https://panelapp-aus.org/api/v1",
    )
    assert origins == frozenset(
        {("panelapp.genomicsengland.co.uk", 443), ("panelapp-aus.org", 443)}
    )


def test_build_origin_allowlist_skips_hostless_values() -> None:
    """A blank or scheme-less base URL contributes no host (never a wildcard)."""
    assert build_origin_allowlist("", "not-a-url") == frozenset()


async def test_guard_allows_https_allowlisted_host() -> None:
    """A plain https request to an allowlisted host passes."""
    guard = make_url_guard(_ALLOWED)
    await guard(httpx.Request("GET", "https://panelapp-aus.org/api/v1/panels/"))


async def test_guard_blocks_non_https_scheme() -> None:
    """An http (downgrade) hop fails closed."""
    guard = make_url_guard(_ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "http://panelapp-aus.org/api/v1/panels/"))


async def test_guard_blocks_userinfo() -> None:
    """A URL carrying userinfo fails closed (credential-smuggling guard)."""
    guard = make_url_guard(_ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://user:pass@panelapp-aus.org/api/v1/"))


async def test_guard_blocks_empty_colon_at_userinfo() -> None:
    """The empty ``:@`` form (username==password=="") still fails closed.

    httpx exposes it as a non-empty ``userinfo`` (``b':'``), so the ANY-userinfo
    check rejects it where a username-or-password check would miss it; a clean
    allowlisted URL still passes.
    """
    guard = make_url_guard(_ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://:@panelapp.genomicsengland.co.uk/x"))
    await guard(httpx.Request("GET", "https://panelapp.genomicsengland.co.uk/api/v1/panels/"))


async def test_guard_blocks_non_allowlisted_host() -> None:
    """A host outside the allowlist fails closed."""
    guard = make_url_guard(_ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://attacker.test/api/v1/panels/"))


async def test_guard_requires_exact_normalized_origin_not_just_host() -> None:
    guard = make_url_guard(frozenset({("panelapp-aus.org", 8443)}))
    await guard(httpx.Request("GET", "https://PANELAPP-AUS.ORG:8443/api/v1/panels/"))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://panelapp-aus.org/api/v1/panels/"))


def test_disallowed_url_error_classifies_non_retryable() -> None:
    """The guard exception maps to a fixed, NON-retryable envelope code."""
    from panelapp_link.mcp.envelope import _classify

    _code, _message, retryable = _classify(DisallowedURLError("blocked"))
    assert retryable is False
    assert str(DisallowedURLError("host in supplied text")) == HTTP_POLICY_ERROR
    assert str(ResponseTooLargeError("host in supplied text")) == HTTP_POLICY_ERROR
