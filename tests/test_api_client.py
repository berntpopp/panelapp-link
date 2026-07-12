"""Tests for the live PanelApp REST client (respx-mocked, no live calls)."""

from __future__ import annotations

import httpx
import pytest
import respx

from panelapp_link.api.client import PanelAppRestClient
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DisallowedURLError, DownloadError, RateLimitError

BASE = "https://panelapp.example.org/api/v1"


def _config(**overrides: object) -> PanelAppDataConfigModel:
    """Build a data config with a small retry budget so tests stay fast."""
    defaults: dict[str, object] = {
        "uk_api_url": BASE,
        "max_retries": 2,
        "max_concurrency": 4,
        "request_timeout": 5,
    }
    defaults.update(overrides)
    return PanelAppDataConfigModel(**defaults)


@pytest.mark.asyncio
@respx.mock
async def test_list_panels_follows_pagination() -> None:
    """list_panels follows DRF ``next`` and concatenates all result pages."""
    page2 = f"{BASE}/panels/?page=2"
    # respx ignores query strings when matching by path, so page 2 must be a
    # distinct route keyed on the ``page`` query param; otherwise the paginator
    # would loop forever re-fetching page 1.
    respx.get(f"{BASE}/panels/", params={"page": "2"}).mock(
        return_value=httpx.Response(
            200,
            json={"count": 4, "next": None, "results": [{"id": 3}, {"id": 4}]},
        )
    )
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 4,
                "next": page2,
                "results": [{"id": 1}, {"id": 2}],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        panels = await client.list_panels(BASE)
    finally:
        await client.aclose()
    assert [p["id"] for p in panels] == [1, 2, 3, 4]


@pytest.mark.asyncio
@respx.mock
async def test_list_signed_off_follows_pagination() -> None:
    """list_signed_off paginates over /panels/signedoff/."""
    respx.get(f"{BASE}/panels/signedoff/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": 3, "version": "4.0", "signed_off": "2023-03-22"}],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        rows = await client.list_signed_off(BASE)
    finally:
        await client.aclose()
    assert rows == [{"id": 3, "version": "4.0", "signed_off": "2023-03-22"}]


@pytest.mark.asyncio
@respx.mock
async def test_get_panel_returns_detail() -> None:
    """get_panel returns the panel detail payload."""
    respx.get(f"{BASE}/panels/1207/").mock(
        return_value=httpx.Response(200, json={"id": 1207, "name": "Acute"})
    )
    client = PanelAppRestClient(_config())
    try:
        detail = await client.get_panel(BASE, 1207)
    finally:
        await client.aclose()
    assert detail["id"] == 1207
    assert detail["name"] == "Acute"


@pytest.mark.asyncio
@respx.mock
async def test_429_retried_then_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated 429s are retried (back-pressure) then raise RateLimitError."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("panelapp_link.api.client.asyncio.sleep", _no_sleep)
    route = respx.get(f"{BASE}/panels/1/").mock(return_value=httpx.Response(429))
    client = PanelAppRestClient(_config(max_retries=2))
    try:
        with pytest.raises(RateLimitError) as exc_info:
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()
    assert exc_info.value.status_code == 429
    # 1 initial attempt + 2 retries == 3 calls (429 is retried, not fatal).
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_429_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient 429 followed by 200 succeeds after backoff."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("panelapp_link.api.client.asyncio.sleep", _no_sleep)
    respx.get(f"{BASE}/panels/7/").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"id": 7})]
    )
    client = PanelAppRestClient(_config(max_retries=3))
    try:
        detail = await client.get_panel(BASE, 7)
    finally:
        await client.aclose()
    assert detail["id"] == 7


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_immediately() -> None:
    """A 403 is a hard denial: raised at once, never retried."""
    route = respx.get(f"{BASE}/panels/1/").mock(return_value=httpx.Response(403))
    client = PanelAppRestClient(_config(max_retries=3))
    try:
        with pytest.raises(RateLimitError) as exc_info:
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()
    assert exc_info.value.status_code == 403
    assert route.call_count == 1


def test_retry_delay_honours_retry_after() -> None:
    """_retry_delay uses the Retry-After hint (capped) when present."""
    from panelapp_link.api.client import _parse_retry_after

    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    delay = PanelAppRestClient._retry_delay(0, 429, 12.0)
    assert 12.0 <= delay <= 13.0


def test_retry_delay_honours_retry_after_60s() -> None:
    """A PanelApp ``Retry-After: 60`` is honoured (not capped at the old 30s)."""
    delay = PanelAppRestClient._retry_delay(0, 429, 60.0)
    assert 60.0 <= delay <= 61.0


def test_retry_delay_caps_retry_after_at_120s() -> None:
    """Retry-After is honoured up to a 120s ceiling, then clamped."""
    from panelapp_link.api.client import _RATE_LIMIT_MAX_SECONDS

    assert _RATE_LIMIT_MAX_SECONDS == 120.0
    delay = PanelAppRestClient._retry_delay(0, 429, 999.0)
    assert 120.0 <= delay <= 121.0


@pytest.mark.asyncio
@respx.mock
async def test_get_genes_by_entity_name_follows_pagination() -> None:
    """get_genes_by_entity_name follows DRF ``next`` across pages and quotes the name."""
    page2 = f"{BASE}/genes/?entity_name=PKD1&page=2"
    respx.get(f"{BASE}/genes/", params={"page": "2"}).mock(
        return_value=httpx.Response(
            200, json={"count": 3, "next": None, "results": [{"entity_name": "PKD1", "panel": {}}]}
        )
    )
    respx.get(f"{BASE}/genes/", params={"entity_name": "PKD1"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 3,
                "next": page2,
                "results": [
                    {"entity_name": "PKD1", "panel": {"id": 1}},
                    {"entity_name": "PKD1", "panel": {"id": 2}},
                ],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        results = await client.get_genes_by_entity_name(BASE, "PKD1")
    finally:
        await client.aclose()
    assert len(results) == 3
    assert all(r["entity_name"] == "PKD1" for r in results)


@pytest.mark.asyncio
@respx.mock
async def test_503_retried_then_download_error() -> None:
    """Repeated 503s exhaust retries and raise DownloadError with status code."""
    route = respx.get(f"{BASE}/panels/2/").mock(return_value=httpx.Response(503))
    client = PanelAppRestClient(_config(max_retries=2))
    try:
        with pytest.raises(DownloadError) as exc_info:
            await client.get_panel(BASE, 2)
    finally:
        await client.aclose()
    assert exc_info.value.status_code == 503
    assert not isinstance(exc_info.value, RateLimitError)
    # 1 initial attempt + 2 retries == 3 calls.
    assert route.call_count == 3


# --- F-17: redirect allowlisting, DRF pagination validation, response caps -------


@pytest.mark.asyncio
@respx.mock
async def test_cross_host_redirect_is_blocked() -> None:
    """A redirect to a non-allowlisted host fails closed via the event hook."""
    respx.get(f"{BASE}/panels/1/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://attacker.test/panels/1/"})
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_https_downgrade_redirect_is_blocked() -> None:
    """A same-host redirect that downgrades to http fails closed."""
    respx.get(f"{BASE}/panels/1/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://panelapp.example.org/api/v1/panels/1/"}
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_with_userinfo_is_blocked() -> None:
    """A redirect that smuggles userinfo fails closed."""
    respx.get(f"{BASE}/panels/1/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://user:pass@panelapp.example.org/api/v1/panels/1/"}
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DisallowedURLError):
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_next_on_different_host_is_rejected() -> None:
    """A DRF ``next`` pointing at a different host fails closed (DownloadError)."""
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                "next": "https://attacker.test/api/v1/panels/?page=2",
                "results": [{"id": 1}],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DownloadError):
            await client.list_panels(BASE)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_next_http_scheme_is_normalized_not_rejected() -> None:
    """A same-host http ``next`` is normalized to https and followed (not rejected)."""
    respx.get(f"{BASE}/panels/", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json={"count": 2, "next": None, "results": [{"id": 2}]})
    )
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                # http (a proxy may emit this) + same host -> normalized to https.
                "next": "http://panelapp.example.org/api/v1/panels/?page=2",
                "results": [{"id": 1}],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        panels = await client.list_panels(BASE)
    finally:
        await client.aclose()
    assert [p["id"] for p in panels] == [1, 2]


@pytest.mark.asyncio
@respx.mock
async def test_response_over_byte_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body over the byte ceiling fails closed (raise) -- never truncated."""
    monkeypatch.setattr("panelapp_link.api.client._MAX_RESPONSE_BYTES", 8)
    respx.get(f"{BASE}/panels/5/").mock(
        return_value=httpx.Response(
            200, json={"id": 5, "name": "a payload that comfortably exceeds eight bytes"}
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DownloadError):
            await client.get_panel(BASE, 5)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_page_ceiling_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exceeding the page ceiling fails loud (never silently truncates the list)."""
    monkeypatch.setattr("panelapp_link.api.client._MAX_PAGES", 1)
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 99,
                "next": f"{BASE}/panels/?page=2",
                "results": [{"id": 1}],
            },
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DownloadError):
            await client.list_panels(BASE)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_row_ceiling_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exceeding the row ceiling fails loud (never silently truncates the list)."""
    monkeypatch.setattr("panelapp_link.api.client._MAX_ROWS", 2)
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={"count": 3, "next": None, "results": [{"id": 1}, {"id": 2}, {"id": 3}]},
        )
    )
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(DownloadError):
            await client.list_panels(BASE)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_single_page_unchanged() -> None:
    """A single-page (``next: null``) list is returned intact under the new guards."""
    respx.get(f"{BASE}/panels/").mock(
        return_value=httpx.Response(
            200, json={"count": 2, "next": None, "results": [{"id": 1}, {"id": 2}]}
        )
    )
    client = PanelAppRestClient(_config())
    try:
        panels = await client.list_panels(BASE)
    finally:
        await client.aclose()
    assert [p["id"] for p in panels] == [1, 2]


@pytest.mark.asyncio
@respx.mock
async def test_injected_client_is_used() -> None:
    """An injected AsyncClient is used as-is and not closed by aclose()."""
    respx.get(f"{BASE}/panels/9/").mock(return_value=httpx.Response(200, json={"id": 9}))
    injected = httpx.AsyncClient()
    client = PanelAppRestClient(_config(), client=injected)
    detail = await client.get_panel(BASE, 9)
    assert detail["id"] == 9
    await client.aclose()
    # aclose must not have closed the injected client.
    assert not injected.is_closed
    await injected.aclose()
