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
    assert body["result"]["only_in"]["1207@uk"] == []
    assert body["result"]["summary"]["n_shared"] == body["result"]["summary"]["n_union"]


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
    assert body["result"]["only_in"]["1207@uk"] == []


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
    assert "AAAS" in body["result"]["genes"]
    assert body["result"]["not_found"] == ["MADEUPGENE"]


@pytest.mark.parametrize(
    ("tool", "args", "field", "step"),
    [
        (
            "resolve_gene",
            {"query": "ENSG00000144285"},
            "query",
            {"tool": "hgnc_resolve_symbol", "arguments": {"query": "ENSG00000144285"}},
        ),
        (
            "get_gene_panels",
            {"gene_symbol": "HGNC:10585"},
            "gene_symbol",
            {"tool": "hgnc_resolve_symbol", "arguments": {"query": "HGNC:10585"}},
        ),
        (
            "get_gene_panels",
            {"gene_symbol": "NCBIGene:6323"},
            "gene_symbol",
            {
                "tool": "hgnc_resolve_gene_by_xref",
                "arguments": {"source": "entrez_id", "value": "6323"},
            },
        ),
        (
            "get_gene_panels",
            {"gene_symbol": "AAAS", "hgnc_id": "AAAS"},
            "hgnc_id",
            {"tool": "hgnc_resolve_symbol", "arguments": {"query": "AAAS"}},
        ),
    ],
)
async def test_gene_id_in_the_wrong_form_is_invalid_input_with_a_converter(
    mcp_client, tool, args, field, step
) -> None:
    async with mcp_client as client:
        body = (await client.call_tool(tool, args, raise_on_error=False)).structured_content
    assert body["error_code"] == "invalid_input"
    assert body["field"] == field
    assert body["allowed_values"]
    assert body["_meta"]["next_commands"] == [step]


async def test_batch_lists_a_gene_id_as_unsupported_and_still_answers(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panels_for_genes",
            {"gene_symbols": ["AAAS", "ENSG00000144285"]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True and "AAAS" in body["result"]["genes"]
    assert body["unsupported"] == [{"input": "ENSG00000144285", "reason": "unsupported_identifier"}]
    assert body["result"]["not_found"] == []
    assert body["_meta"]["next_commands"][0]["tool"] == "hgnc_resolve_symbol"


async def test_paged_tool_reports_pagination_meta(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panel_genes", {"panel_id": 1207, "region": "uk", "limit": 1}, raise_on_error=False
        )
    body = res.structured_content
    pagination = body["_meta"]["pagination"]
    assert pagination["total_count"] == body["total"]
    assert pagination["has_more"] is body["has_more"]
    assert pagination["next_cursor"] == (body.get("truncated") or {}).get("next_cursor")
