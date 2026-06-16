"""Tests for the live PanelApp REST client (respx-mocked, no live calls)."""

from __future__ import annotations

import httpx
import pytest
import respx

from panelapp_link.api.client import PanelAppRestClient
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DownloadError, RateLimitError

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
