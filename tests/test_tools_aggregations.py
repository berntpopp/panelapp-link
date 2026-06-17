"""WS-2/WS-3 end-to-end through the MCP client over the fixture service."""

from __future__ import annotations

import pytest
from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing


@pytest.fixture
def mcp_client(live_service):
    set_service_for_testing(live_service)
    try:
        yield Client(create_panelapp_mcp())
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


async def test_compare_panels_self_overlap(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": 1207, "region": "uk"}, {"panel_id": 1207, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert body["only_in"]["1207@uk"] == []
    assert body["summary"]["n_shared"] == body["summary"]["n_union"]


async def test_compare_panels_region_both_rejected(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": 1207, "region": "both"}, {"panel_id": 285, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is False
    assert body["error_code"] == "invalid_input"
    assert body["field_errors"][0]["field"] == "region"


async def test_get_panels_for_genes_mixed(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panels_for_genes",
            {"gene_symbols": ["AAAS", "MADEUPGENE"], "region": "uk"},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert "AAAS" in body["genes"]
    assert body["not_found"] == ["MADEUPGENE"]
