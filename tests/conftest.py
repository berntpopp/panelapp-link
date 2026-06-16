"""Shared pytest fixtures for the PanelApp-Link test suite.

The service is a pure live-API client, so tests serve the committed PanelApp JSON
fixtures over a respx-mocked ``httpx.AsyncClient`` (no live network). The
``live_service`` fixture wires that mocked client into a real
:class:`PanelAppService` so service and tool tests exercise the full live path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from panelapp_link.api.client import PanelAppRestClient
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.services.panelapp_service import PanelAppService

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Test base URLs (distinct hosts so respx can route per region without hitting
# the real PanelApp servers).
UK_BASE = "https://uk.panelapp.test/api/v1"
AU_BASE = "https://au.panelapp.test/api/v1"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a committed JSON fixture by filename from ``tests/fixtures``."""
    with (FIXTURES_DIR / name).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _page(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of rows in a single-page DRF envelope."""
    return {"count": len(results), "next": None, "previous": None, "results": results}


def _data_config() -> PanelAppDataConfigModel:
    """A data config pointed at the test base URLs with a small retry budget."""
    return PanelAppDataConfigModel(
        uk_api_url=UK_BASE,
        au_api_url=AU_BASE,
        max_retries=1,
        max_concurrency=4,
        request_timeout=5,
        cache_ttl=3600,
        cache_size=512,
    )


def _mount_region(
    router: respx.MockRouter,
    base: str,
    *,
    panels_page: dict[str, Any],
    signedoff_page: dict[str, Any],
    details: dict[int, dict[str, Any]],
    genes: dict[str, dict[str, Any]],
) -> None:
    """Register the panel-list, signed-off, panel-detail, and /genes/ routes."""
    router.get(f"{base}/panels/").mock(return_value=httpx.Response(200, json=panels_page))
    router.get(f"{base}/panels/signedoff/").mock(
        return_value=httpx.Response(200, json=signedoff_page)
    )
    for panel_id, detail in details.items():
        router.get(f"{base}/panels/{panel_id}/").mock(return_value=httpx.Response(200, json=detail))

    def _genes_handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlsplit(str(request.url)).query)
        name = (params.get("entity_name") or [""])[0].upper()
        return httpx.Response(200, json=genes.get(name, _page([])))

    router.get(url__startswith=f"{base}/genes/").mock(side_effect=_genes_handler)
    # 404 fallback for any unmocked panel detail so NotFound paths are testable.
    router.get(url__regex=rf"^{base}/panels/\d+/$").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )


def build_router() -> respx.MockRouter:
    """Build a respx router serving both regions from the committed fixtures."""
    router = respx.mock(assert_all_called=False, base_url=None)

    uk_genes = {"AAAS": _au_like_uk_genes(), "HMBS": _uk_genes_hmbs()}
    au_genes = {"PKD1": load_fixture("au_genes_pkd1.json")}

    _mount_region(
        router,
        UK_BASE,
        panels_page=load_fixture("uk_panels_page1.json"),
        signedoff_page=load_fixture("uk_signedoff_page1.json"),
        details={1207: load_fixture("uk_panel_1207.json"), 285: load_fixture("uk_panel_285.json")},
        genes=uk_genes,
    )
    _mount_region(
        router,
        AU_BASE,
        panels_page=load_fixture("au_panels_page1.json"),
        signedoff_page=_page([]),
        details={3149: load_fixture("au_panel_3149.json")},
        genes=au_genes,
    )
    return router


def _gene_result(entity: dict[str, Any], panel: dict[str, Any]) -> dict[str, Any]:
    """Wrap a panel-detail entity as a /genes/ result (entity + full panel)."""
    result = dict(entity)
    result["panel"] = panel
    return result


def _uk_genes_hmbs() -> dict[str, Any]:
    """A /genes/?entity_name=HMBS page derived from the uk_panel_1207 fixture."""
    detail = load_fixture("uk_panel_1207.json")
    panel = {k: v for k, v in detail.items() if k not in ("genes", "regions", "strs")}
    return _page([_gene_result(detail["genes"][0], panel)])


def _au_like_uk_genes() -> dict[str, Any]:
    """A /genes/?entity_name=AAAS page derived from the uk_panel_285 fixture."""
    detail = load_fixture("uk_panel_285.json")
    panel = {k: v for k, v in detail.items() if k not in ("genes", "regions", "strs")}
    aaas = next(g for g in detail["genes"] if g["entity_name"] == "AAAS")
    return _page([_gene_result(aaas, panel)])


@pytest.fixture
async def live_service() -> AsyncIterator[PanelAppService]:
    """A PanelAppService over a respx-mocked httpx client serving the fixtures."""
    router = build_router()
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = PanelAppRestClient(_data_config(), client=http_client)
        service = PanelAppService(client, _data_config(), cache_ttl=3600, cache_size=512)
        yield service
