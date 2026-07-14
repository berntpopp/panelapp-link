"""Every advertised contract must match what the server actually does.

Same defect class as the `region` enum bug this branch fixes, on the prose
surfaces: a doc, a capabilities string, or a resource note that promises an
argument, a page cursor, a response field, or an error code the runtime does not
honour is a trap -- an agent that believes it fails. The oracle is always the live
server (`create_panelapp_mcp()`), never a hardcoded copy of the contract.

Guards here:
  * every ``tool(arg, ...)`` example in the docs and in the agent-facing prose uses
    only arguments the tool's LIVE input schema declares;
  * an hgnc id cannot stand alone as a query (PanelApp is keyed on gene symbol);
  * the documented error codes are exactly the advertised ones;
  * the tools documented as cursor-paged are exactly the ones with a `cursor`;
  * ``headline`` is only claimed for the tool whose output schema declares it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastmcp import Client

from panelapp_link.mcp.capabilities import build_capabilities
from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.resources import (
    PANELAPP_REFERENCE_NOTES,
    PANELAPP_SERVER_INSTRUCTIONS,
    PANELAPP_USAGE_NOTES,
)

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "usage.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"


async def _tool_schemas() -> dict[str, dict[str, Any]]:
    """Each tool's LIVE input schema, keyed by tool name."""
    mcp = create_panelapp_mcp()
    return {tool.name: (tool.parameters or {}) for tool in await mcp.list_tools()}


async def _output_schemas() -> dict[str, dict[str, Any]]:
    mcp = create_panelapp_mcp()
    return {tool.name: (tool.output_schema or {}) for tool in await mcp.list_tools()}


def _documented_args(prose: str, tool: str) -> set[str]:
    """Argument names used in every ``tool(...)`` example in a prose surface.

    Nested structures (``panels=[{panel_id, region}]``) and quoted values
    (``min_confidence='green'``) are stripped first, so only TOP-LEVEL argument
    names are compared against the tool's declared properties.
    """
    args: set[str] = set()
    for call in re.findall(rf"\b{re.escape(tool)}\(([^)]*)\)", prose):
        flat = re.sub(r"\[[^\]]*\]|\{[^}]*\}|'[^']*'|\"[^\"]*\"", "", call)
        args |= set(re.findall(r"[a-z_][a-z0-9_]*", flat))
    return args


async def test_documented_tool_calls_use_only_declared_arguments() -> None:
    """A `tool(arg=...)` example must not name an argument the schema rejects.

    `resolve_gene(query | gene_symbol | hgnc_id)` was the trap: `resolve_gene` has
    no `hgnc_id` argument at all, so an agent following the docs got an unknown-arg
    rejection.
    """
    schemas = await _tool_schemas()
    surfaces = {
        "README.md": README.read_text(encoding="utf-8"),
        "docs/usage.md": USAGE.read_text(encoding="utf-8"),
        "docs/architecture.md": ARCHITECTURE.read_text(encoding="utf-8"),
        "capabilities": " ".join(build_capabilities()["recommended_workflows"]),
        "panelapp://usage": PANELAPP_USAGE_NOTES,
        "server instructions": PANELAPP_SERVER_INSTRUCTIONS,
        "panelapp://reference": PANELAPP_REFERENCE_NOTES,
    }
    for label, prose in surfaces.items():
        for tool, schema in schemas.items():
            declared = set(schema.get("properties", {}))
            used = _documented_args(prose, tool)
            assert used <= declared, (
                f"{label} documents {tool}({', '.join(sorted(used - declared))}) but the "
                f"live schema declares only {sorted(declared)}"
            )


async def test_hgnc_id_cannot_stand_alone_as_a_query() -> None:
    """PanelApp is keyed on gene SYMBOL: an hgnc id alone cannot drive a query.

    So the schema must not advertise a query the runtime refuses: `resolve_gene`
    takes no `hgnc_id`, and `get_gene_panels` REQUIRES `gene_symbol` (`hgnc_id` is
    an optional result filter). Otherwise `get_gene_panels(hgnc_id=...)` is
    schema-accepted and then rejected at runtime -- the original bug, again.
    """
    schemas = await _tool_schemas()
    assert "hgnc_id" not in schemas["resolve_gene"].get("properties", {})

    gene_panels = schemas["get_gene_panels"]
    assert "hgnc_id" in gene_panels["properties"]  # still an optional filter
    assert "gene_symbol" in gene_panels.get("required", []), (
        "get_gene_panels must require gene_symbol; the service rejects hgnc-only "
        "input, so the schema must not advertise it as a standalone query"
    )

    async with Client(create_panelapp_mcp()) as client:
        res = await client.call_tool(
            "get_gene_panels", {"hgnc_id": "HGNC:1100"}, raise_on_error=False
        )
    body = res.structured_content
    assert body["error_code"] == "invalid_input"
    assert body["field_errors"][0]["field"] == "gene_symbol"


def _codes_in(prose: str) -> set[str]:
    """Error codes named in a prose surface (they are the only `snake_case` codes)."""
    known = set(build_capabilities()["error_codes_list"]) | {"ambiguous_query"}
    return {code for code in known if re.search(rf"\b{code}\b", prose)}


async def test_documented_error_codes_match_the_advertised_taxonomy() -> None:
    """The docs/resource error taxonomy must equal what capabilities advertises.

    `ambiguous_query` was documented but no `_classify` branch ever emits it, and
    `limit_exceeded` (which IS emitted) was missing from both prose surfaces.
    """
    advertised = set(build_capabilities()["error_codes_list"])

    taxonomy = ARCHITECTURE.read_text(encoding="utf-8").split("## Error taxonomy")[1]
    assert _codes_in(taxonomy.split("##")[0]) == advertised

    codes_sentence = PANELAPP_REFERENCE_NOTES.split("Error codes:")[1]
    assert _codes_in(codes_sentence.split(".")[0]) == advertised


async def test_cursor_paged_tools_are_exactly_the_ones_documented_as_paged() -> None:
    """`get_gene_panels` was advertised as cursor-paged; it has no `cursor` at all.

    An agent following panelapp://usage or panelapp://reference would call
    `get_gene_panels(cursor=...)` and be rejected for an unknown argument.
    """
    schemas = await _tool_schemas()
    paged = {name for name, s in schemas.items() if "cursor" in s.get("properties", {})}
    assert paged  # sanity: the class is non-empty

    claims = {
        "panelapp://usage": PANELAPP_USAGE_NOTES.split("Paged tools (")[1].split(")")[0],
        "panelapp://reference": PANELAPP_REFERENCE_NOTES.split("Paging contract:")[1].split(".")[0],
    }
    for label, sentence in claims.items():
        named = {name for name in schemas if re.search(rf"\b{re.escape(name)}\b", sentence)}
        assert named == paged, (
            f"{label} lists the paged tools as {sorted(named)}; the live schemas say "
            f"the cursor-paged tools are {sorted(paged)}"
        )


async def test_headline_is_only_claimed_where_the_output_schema_declares_it() -> None:
    """Only the diagnostics response carries a `headline`; the docs claimed all did."""
    with_headline = {
        name
        for name, schema in (await _output_schemas()).items()
        if "headline" in schema.get("properties", {})
    }
    assert with_headline == {"get_panelapp_diagnostics"}

    usage = USAGE.read_text(encoding="utf-8")
    hits = [line for line in usage.splitlines() if "headline" in line]
    assert hits, "docs/usage.md no longer mentions headline; drop this guard"
    for line in hits:
        assert "get_panelapp_diagnostics" in line, (
            "docs/usage.md must attribute `headline` to get_panelapp_diagnostics -- "
            "no other tool emits it"
        )
