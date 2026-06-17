"""End-to-end MCP tool tests via an in-memory fastmcp client.

These build a bare ``FastMCP``, register the tool modules on it, inject a
respx-backed live :class:`PanelAppService` via ``set_service_for_testing``, and
drive the tools through an in-memory ``fastmcp.Client``. Each tool's response is
asserted at the envelope level (``success`` + payload keys). The service methods
are async; the tool layer awaits them. No live network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp import Client, FastMCP

from panelapp_link.mcp.service_adapters import (
    reset_panelapp_service,
    set_service_for_testing,
)
from panelapp_link.mcp.tools import register_all_tools
from panelapp_link.services.panelapp_service import PanelAppService

pytestmark = pytest.mark.mcp


@pytest.fixture
async def mcp_client(live_service: PanelAppService) -> AsyncIterator[Client]:
    """An in-memory fastmcp client with all tools registered + live service injected."""
    set_service_for_testing(live_service)
    mcp: FastMCP = FastMCP("panelapp-link-test")
    register_all_tools(mcp)
    try:
        async with Client(mcp) as client:
            yield client
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


# --- happy path: each of the 7 tools returns success + expected payload keys ---


async def test_search_panels_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("search_panels", {"query": "", "region": "both"})
    data = result.structured_content
    assert data["success"] is True
    assert {"query", "count", "total", "panels"} <= set(data)
    assert isinstance(data["panels"], list)
    assert "next_commands" in data["_meta"]


async def test_search_panels_region_both_merges_uk_and_au(mcp_client: Client) -> None:
    uk = (await mcp_client.call_tool("search_panels", {"region": "uk"})).structured_content
    au = (await mcp_client.call_tool("search_panels", {"region": "australia"})).structured_content
    both = (await mcp_client.call_tool("search_panels", {"region": "both"})).structured_content
    assert uk["success"] and au["success"] and both["success"]
    assert uk["total"] > 0 and au["total"] > 0
    assert both["total"] == uk["total"] + au["total"]


async def test_get_panel_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_panel", {"panel_id": 285, "region": "uk"})
    data = result.structured_content
    assert data["success"] is True
    assert data["panel"]["panel_id"] == 285
    assert data["panel"]["region"] == "uk"
    assert data["_meta"]["next_commands"][0]["tool"] == "get_panel_genes"


async def test_get_panel_genes_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_panel_genes", {"panel_id": 285, "region": "uk"})
    data = result.structured_content
    assert data["success"] is True
    assert data["entity_type"] == "gene"
    assert {"panel_id", "region", "count", "total", "entities"} <= set(data)
    assert data["total"] >= 1
    assert all(e.get("entity_type") == "gene" for e in data["entities"])


async def test_get_panel_genes_entity_type_filter(mcp_client: Client) -> None:
    genes = (
        await mcp_client.call_tool(
            "get_panel_genes", {"panel_id": 285, "region": "uk", "entity_type": "gene"}
        )
    ).structured_content
    regions = (
        await mcp_client.call_tool(
            "get_panel_genes", {"panel_id": 285, "region": "uk", "entity_type": "region"}
        )
    ).structured_content
    strs = (
        await mcp_client.call_tool(
            "get_panel_genes", {"panel_id": 285, "region": "uk", "entity_type": "str"}
        )
    ).structured_content
    allents = (
        await mcp_client.call_tool(
            "get_panel_genes", {"panel_id": 285, "region": "uk", "entity_type": "all"}
        )
    ).structured_content
    assert genes["success"] and regions["success"] and strs["success"] and allents["success"]
    assert regions["total"] >= 1 and strs["total"] >= 1
    assert allents["total"] == genes["total"] + regions["total"] + strs["total"]
    assert all(e["entity_type"] == "region" for e in regions["entities"])
    assert all(e["entity_type"] == "str" for e in strs["entities"])


async def test_get_panel_genes_min_confidence_filter(mcp_client: Client) -> None:
    unfiltered = (
        await mcp_client.call_tool("get_panel_genes", {"panel_id": 285, "region": "uk"})
    ).structured_content
    green = (
        await mcp_client.call_tool(
            "get_panel_genes", {"panel_id": 285, "region": "uk", "min_confidence": "green"}
        )
    ).structured_content
    assert green["success"] is True
    assert green["total"] <= unfiltered["total"]
    assert all(e["confidence_label"] == "green" for e in green["entities"])


async def test_resolve_gene_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("resolve_gene", {"query": "AAAS"})
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "AAAS"
    assert {"query", "gene", "matches"} <= set(data)
    # The breadcrumb must drive get_gene_panels by gene_symbol (the query key),
    # not hgnc_id -- following it verbatim must succeed.
    breadcrumb = data["_meta"]["next_commands"][0]
    assert breadcrumb["tool"] == "get_gene_panels"
    assert breadcrumb["arguments"] == {"gene_symbol": "AAAS"}


async def test_resolve_gene_breadcrumb_is_followable(mcp_client: Client) -> None:
    """B-1 headline regression: following resolve_gene's own next_commands[0]
    verbatim into get_gene_panels must return success (contract self-consistent)."""
    resolved = (await mcp_client.call_tool("resolve_gene", {"query": "AAAS"})).structured_content
    step = resolved["_meta"]["next_commands"][0]
    followed = (await mcp_client.call_tool(step["tool"], step["arguments"])).structured_content
    assert followed["success"] is True
    assert followed["gene"]["gene_symbol"] == "AAAS"


async def test_get_gene_panels_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_gene_panels", {"gene_symbol": "AAAS"})
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "AAAS"
    assert {"gene", "count", "panels"} <= set(data)
    assert data["count"] >= 1


async def test_get_gene_panels_australia(mcp_client: Client) -> None:
    result = await mcp_client.call_tool(
        "get_gene_panels", {"gene_symbol": "PKD1", "region": "australia"}
    )
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "PKD1"
    assert data["gene"]["hgnc_id"] == "HGNC:9008"


async def test_get_server_capabilities_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_server_capabilities", {})
    data = result.structured_content
    assert data["success"] is True
    assert len(data["tools"]) == 9
    assert "capabilities_version" in data
    assert data["data"]["mode"] == "live"


async def test_get_panelapp_diagnostics_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_panelapp_diagnostics", {})
    data = result.structured_content
    assert data["success"] is True
    assert data["data"]["mode"] == "live"
    assert "uk" in data["data"]["sources"]
    assert "capabilities_version" in data


async def test_diagnostics_surfaces_red_metrics(mcp_client: Client) -> None:
    await mcp_client.call_tool("search_panels", {"region": "uk"})
    data = (await mcp_client.call_tool("get_panelapp_diagnostics", {})).structured_content
    metrics = data["data"]["metrics"]
    assert "cache" in metrics
    assert "tool_duration_ms" in metrics
    assert metrics["requests_total"] >= 1


async def test_tool_meta_surfaces_cache_and_upstream_timing(mcp_client: Client) -> None:
    data = (await mcp_client.call_tool("search_panels", {"region": "both"})).structured_content
    meta = data["_meta"]
    # Cold call: the heavy double-fetch shows up as a cache miss + upstream timing.
    assert meta["cache"] in {"miss", "partial"}
    assert meta["upstream_ms"] >= 0
    assert set(meta["upstream"]) <= {"uk", "australia"}


# --- bad input -> success:false + invalid_input ---


async def test_get_panel_region_both_is_invalid_input(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_panel", {"panel_id": 285, "region": "both"})
    data = result.structured_content
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
    assert data["field_errors"][0]["field"] == "region"


async def test_resolve_gene_no_args_is_invalid_input(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("resolve_gene", {})
    data = result.structured_content
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"


async def test_get_gene_panels_unknown_gene_is_not_found(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_gene_panels", {"gene_symbol": "NOTAGENE"})
    data = result.structured_content
    assert data["success"] is False
    assert data["error_code"] == "not_found"


async def test_request_id_and_timing_present(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("search_panels", {"query": ""})
    meta = result.structured_content["_meta"]
    assert isinstance(meta["request_id"], str)
    assert isinstance(meta["elapsed_ms"], (int, float))
    assert meta["unsafe_for_clinical_use"] is True
