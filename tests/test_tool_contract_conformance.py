"""Tool-layer contract guard: shared count fields are mode-invariant end-to-end."""

from __future__ import annotations

import pytest
from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing
from panelapp_link.models.enums import RESPONSE_MODES


@pytest.fixture
def mcp_client(live_service):
    set_service_for_testing(live_service)
    try:
        yield Client(create_panelapp_mcp())
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_get_panel_count_fields_mode_invariant(mcp_client, mode: str) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panel",
            {"panel_id": 1207, "region": "uk", "response_mode": mode},
            raise_on_error=False,
        )
    panel = res.structured_content["panel"]
    assert {"n_genes", "n_regions", "n_strs"} <= panel.keys()
    assert not any(k.startswith("number_of_") for k in panel)


@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_search_panels_count_fields_mode_invariant(mcp_client, mode: str) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "search_panels",
            {"query": "", "region": "uk", "limit": 3, "response_mode": mode},
            raise_on_error=False,
        )
    for panel in res.structured_content["panels"]:
        assert {"n_genes", "n_regions", "n_strs"} <= panel.keys()
        assert not any(k.startswith("number_of_") for k in panel)
