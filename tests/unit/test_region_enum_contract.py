"""The advertised `region` enum must equal what the runtime actually accepts.

A JSON-Schema ``enum`` is a *constraint*; a ``description`` is not. ``get_panel``
and ``get_panel_genes`` advertised ``["uk","australia","both"]`` while the service
rejects ``"both"`` (panel ids are per-region), so an agent that read the schema,
saw ``both`` was legal, and sent it, paid a round trip for an ``invalid_input``.
The same class of defect hid in ``compare_panels``, whose ``panels[]`` items were
a freeform ``dict[str, Any]`` even though each ref must be ``{panel_id, region}``
with a concrete region.

The oracle is the real server built by ``create_panelapp_mcp()``: these guards
introspect the LIVE input schemas, never a hardcoded copy.
"""

from __future__ import annotations

from typing import Any

import pytest

from panelapp_link.mcp.facade import create_panelapp_mcp

# Panel ids are region-scoped: these tools take one concrete region, never "both".
_CONCRETE_REGION_TOOLS = ("get_panel", "get_panel_genes")
# These fan out over both instances, so "both" is a legal (and default) value.
_FAN_OUT_REGION_TOOLS = ("search_panels", "get_gene_panels", "resolve_gene", "get_panels_for_genes")

_CONCRETE_REGIONS = {"uk", "australia"}


async def _input_schemas() -> dict[str, dict[str, Any]]:
    """Every tool's LIVE input schema, keyed by tool name."""
    mcp = create_panelapp_mcp()
    return {tool.name: (tool.parameters or {}) for tool in await mcp.list_tools()}


def _deref(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local ``$ref`` against the schema's ``$defs`` (one hop is enough)."""
    ref = node.get("$ref")
    if not ref:
        return node
    name = ref.rsplit("/", 1)[-1]
    resolved: dict[str, Any] = schema.get("$defs", {})[name]
    return resolved


@pytest.mark.parametrize("tool_name", _CONCRETE_REGION_TOOLS)
async def test_concrete_region_tools_advertise_only_concrete_regions(tool_name: str) -> None:
    """The advertised enum must be exactly what the service accepts: no 'both'."""
    schema = (await _input_schemas())[tool_name]
    region = schema["properties"]["region"]
    assert set(region["enum"]) == _CONCRETE_REGIONS, (
        f"{tool_name} advertises region={region['enum']} but its service rejects "
        "'both' (panel ids are per-region); the schema and the runtime must agree"
    )
    assert "region" in schema.get("required", [])


@pytest.mark.parametrize("tool_name", _FAN_OUT_REGION_TOOLS)
async def test_fan_out_region_tools_still_offer_both(tool_name: str) -> None:
    """The cross-region tools legitimately take 'both' -- don't over-tighten them."""
    region = (await _input_schemas())[tool_name]["properties"]["region"]
    assert set(region["enum"]) == _CONCRETE_REGIONS | {"both"}
    assert region["default"] == "both"


async def test_compare_panels_advertises_typed_concrete_region_refs() -> None:
    """`panels[]` must advertise {panel_id: int, region: uk|australia}, not a bare dict."""
    schema = (await _input_schemas())["compare_panels"]
    item = _deref(schema, schema["properties"]["panels"]["items"])
    props = item.get("properties", {})
    assert set(props) == {"panel_id", "region"}, (
        "compare_panels must advertise the shape of a panel ref; a freeform "
        f"dict gives an agent no guidance (got {sorted(props)})"
    )
    assert props["panel_id"]["type"] == "integer"
    assert set(props["region"]["enum"]) == _CONCRETE_REGIONS
    assert set(item.get("required", [])) == {"panel_id", "region"}
