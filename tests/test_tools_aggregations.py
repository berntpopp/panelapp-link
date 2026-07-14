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
    """A 'both' ref is rejected -- now by the schema (`panels[]` is a typed ref).

    Same caller-visible envelope as before; the field is the pydantic location of
    the offending ref, which pins the bad element instead of just naming `region`.
    """
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": 1207, "region": "both"}, {"panel_id": 285, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is False
    assert body["error_code"] == "invalid_input"
    assert body["field_errors"][0]["field"] == "panels.0.region"


async def test_compare_panels_accepts_a_ref_with_extra_keys(mcp_client) -> None:
    """A panel object handed straight back from search_panels / get_panel must work.

    The typed ref must not be STRICTER than the service was: `_validate_refs` accepted
    any dict and stripped it to {panel_id, region}, and an agent will very plausibly
    feed a whole panel row (name, n_genes, ...) from a previous result back into
    compare_panels. `extra="ignore"` keeps that accept-and-strip behaviour -- and keeps
    the schema honest, since pydantic advertises `additionalProperties: false` only for
    `extra="forbid"`.
    """
    row = {"panel_id": 1207, "region": "uk", "name": "Cystic renal disease", "n_genes": 3}
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [row, {"panel_id": 1207, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert body["only_in"]["1207@uk"] == []


async def test_compare_panels_coerces_a_string_panel_id(mcp_client) -> None:
    """`panel_id` keeps pydantic's coercion (NOT StrictInt): "1207" is accepted.

    The runtime is deliberately MORE permissive than the advertised `type: integer`.
    That is the safe direction -- a caller that obeys the schema always succeeds, and
    LLM callers do emit "1207" for integers. The bug this branch fixes is the reverse:
    a schema that promises a value ('both') the runtime refuses.
    """
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": "1207", "region": "uk"}, {"panel_id": 1207, "region": "uk"}]},
            raise_on_error=False,
        )
    assert res.structured_content["success"] is True


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
