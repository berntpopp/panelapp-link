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

import re
from typing import Any

import pytest
from fastmcp import Client

from panelapp_link.mcp.capabilities import build_capabilities
from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.resources import PANELAPP_REFERENCE_NOTES

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
    # ... but NOT stricter than the runtime: the service accepted a ref carrying extra
    # keys and stripped it, so the schema must not advertise additionalProperties:false
    # (which is what pydantic emits for extra="forbid"). Schema == runtime, both ways.
    assert item.get("additionalProperties") is not False


async def test_rejected_region_message_names_the_allowed_options() -> None:
    """A caller who sends 'both' must still learn what IS allowed.

    Rejection moved to the schema boundary, where pydantic errors map to FIXED
    reasons -- so a bare "Value is not one of the allowed options." would be a
    usability regression against the service's old explanatory message. The reason
    must name the allowed values (server-authored, from our own Literal).
    """
    async with Client(create_panelapp_mcp()) as client:
        res = await client.call_tool(
            "get_panel", {"panel_id": 285, "region": "both"}, raise_on_error=False
        )
    body = res.structured_content
    assert body["error_code"] == "invalid_input"
    reason = body["field_errors"][0]["reason"]
    assert "uk" in reason and "australia" in reason, reason
    assert body["message"].startswith("Invalid input -- `region`:")
    # the rejected value is never echoed back (error-message sanitation contract)
    assert "both" not in reason


def _concrete_region_sentence(prose: str, label: str) -> str:
    """The one sentence of a prose surface that states the concrete-region rule."""
    flat = " ".join(prose.split())
    hits = [s for s in re.split(r"(?<=\.)\s+", flat) if "concrete region" in s]
    assert len(hits) == 1, f"expected exactly one 'concrete region' sentence in {label}: {hits}"
    return hits[0]


async def test_prose_surfaces_name_every_concrete_region_tool() -> None:
    """capabilities + the reference resource must name EXACTLY the concrete-region tools.

    An agent that reads the capabilities/reference prose instead of the tool schema
    must not be able to infer that `get_panel_genes(region='both')` is fine -- it has
    the same constraint as `get_panel`. The expected set is derived from the LIVE
    schemas, so this cannot rot when a tool joins or leaves the class.
    """
    schemas = await _input_schemas()
    concrete = {
        name
        for name, schema in schemas.items()
        if set((schema.get("properties", {}).get("region") or {}).get("enum", []))
        == _CONCRETE_REGIONS
    }
    assert concrete == set(_CONCRETE_REGION_TOOLS)  # sanity: the oracle sees the class

    surfaces = {
        "capabilities.parameter_conventions.region": build_capabilities()["parameter_conventions"][
            "region"
        ],
        "resources.PANELAPP_REFERENCE_NOTES": PANELAPP_REFERENCE_NOTES,
    }
    for label, prose in surfaces.items():
        sentence = _concrete_region_sentence(prose, label)
        named = {name for name in schemas if re.search(rf"\b{re.escape(name)}\b", sentence)}
        assert named == concrete, (
            f"{label} must name exactly the tools whose schema requires a concrete "
            f"region ({sorted(concrete)}); it names {sorted(named)}"
        )
