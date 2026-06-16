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
async def test_429_raises_rate_limit_error() -> None:
    """A 429 status raises RateLimitError immediately (no infinite retry)."""
    respx.get(f"{BASE}/panels/1/").mock(return_value=httpx.Response(429))
    client = PanelAppRestClient(_config())
    try:
        with pytest.raises(RateLimitError):
            await client.get_panel(BASE, 1)
    finally:
        await client.aclose()


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
