"""End-to-end MCP tool tests via an in-memory fastmcp client.

W9 (facade/server) is not built yet, so these tests build a bare ``FastMCP``,
register the W7 tool modules on it, inject a built_db-backed service via
``set_service_for_testing``, and drive the tools through an in-memory
``fastmcp.Client`` -- the same connected-client mechanism the fleet siblings use,
minus the facade. Each tool's response is asserted at the envelope level
(``success`` + payload keys).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp import Client, FastMCP

from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.mcp.service_adapters import (
    reset_panelapp_service,
    set_service_for_testing,
)
from panelapp_link.mcp.tools import register_all_tools
from panelapp_link.services.panelapp_service import PanelAppService

pytestmark = pytest.mark.mcp


@pytest.fixture
def service(repository: PanelAppRepository) -> PanelAppService:
    """A PanelAppService over the built_db-backed repository (cache enabled)."""
    return PanelAppService(repository, cache_size=512, cache_ttl=3600)


@pytest.fixture
async def mcp_client(service: PanelAppService) -> AsyncIterator[Client]:
    """An in-memory fastmcp client with all W7 tools registered + service injected."""
    set_service_for_testing(service)
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
    # both is the deduped union of the two single-region result sets.
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
    # uk panel 285 carries genes + regions + strs; "all" is the sum of the parts.
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
    # green-only is a subset of the unfiltered set, and every hit is green.
    assert green["total"] <= unfiltered["total"]
    assert all(e["confidence_label"] == "green" for e in green["entities"])


async def test_resolve_gene_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("resolve_gene", {"query": "AAAS"})
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "AAAS"
    assert {"query", "gene", "matches"} <= set(data)
    # resolve_gene chains into get_gene_panels.
    assert data["_meta"]["next_commands"][0]["tool"] == "get_gene_panels"


async def test_get_gene_panels_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_gene_panels", {"gene_symbol": "AAAS"})
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "AAAS"
    assert {"gene", "count", "panels"} <= set(data)
    assert data["count"] >= 1


async def test_get_gene_panels_by_hgnc_id(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_gene_panels", {"hgnc_id": "HGNC:13666"})
    data = result.structured_content
    assert data["success"] is True
    assert data["gene"]["gene_symbol"] == "AAAS"


async def test_get_server_capabilities_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_server_capabilities", {})
    data = result.structured_content
    assert data["success"] is True
    assert len(data["tools"]) == 7
    assert "capabilities_version" in data
    assert "data" in data


async def test_get_panelapp_diagnostics_success(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_panelapp_diagnostics", {})
    data = result.structured_content
    assert data["success"] is True
    # diagnostics reads the injected (built_db) service meta.
    assert data["data"]["schema_version"] == "1"
    assert data["data"]["uk_panel_count"] >= 1
    assert "capabilities_version" in data
    assert "refresh" in data


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
