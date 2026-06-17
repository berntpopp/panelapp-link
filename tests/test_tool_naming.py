"""Tool-naming guard: every registered MCP tool conforms to Tool-Naming Standard v1.

W7 registers the tools directly on a bare ``FastMCP`` (the W9 facade does not
exist yet), so these tests build a fresh instance, register everything, and list
the live tool objects. Per the GeneFoundry Tool-Naming & Normalization Standard
v1 (rule 8), every name must match the length-bounded charset
``^[a-z0-9_]{1,50}$`` and start with a canonical verb, must equal the
fleet-frozen expected set, and must carry a domain tag. Namespacing is the
gateway's job, so leaf names stay UNPREFIXED (no ``panelapp_`` self-prefix).
"""

from __future__ import annotations

import re

import pytest
from fastmcp import FastMCP

from panelapp_link.mcp.capabilities import TOOLS
from panelapp_link.mcp.tools import register_all_tools

pytestmark = pytest.mark.mcp

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Standard v1 (rule 8): length-bounded charset + canonical verb start.
STANDARD_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
CANONICAL_VERBS = frozenset({"get", "search", "list", "resolve", "find", "compare", "compute"})
NAMESPACE = "panelapp"

EXPECTED_TOOLS = {
    "search_panels",
    "get_panel",
    "get_panel_genes",
    "get_gene_panels",
    "resolve_gene",
    "compare_panels",
    "get_panels_for_genes",
    "get_server_capabilities",
    "get_panelapp_diagnostics",
}


async def _registered_tools() -> list:
    """The live tool objects (name + tags) registered by W7."""
    mcp: FastMCP = FastMCP("panelapp-link-test")
    register_all_tools(mcp)
    return await mcp.list_tools()


async def test_every_tool_name_is_snake_case() -> None:
    tools = await _registered_tools()
    assert tools, "no tools registered"
    bad = [t.name for t in tools if not TOOL_NAME_RE.match(t.name)]
    assert not bad, f"non-snake_case tool names: {bad}"


async def test_tool_names_conform_to_standard_v1() -> None:
    """Rule 8: length-bounded charset, canonical verb start, no self-prefix."""
    tools = await _registered_tools()
    names = sorted(t.name for t in tools)
    assert names, "no tools registered"
    for name in names:
        assert STANDARD_NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert name.split("_", 1)[0] in CANONICAL_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(CANONICAL_VERBS)}"
        )
        assert not name.startswith(f"{NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{NAMESPACE}' namespace token"
        )


async def test_registered_tools_equal_expected_set() -> None:
    names = {t.name for t in await _registered_tools()}
    assert names == EXPECTED_TOOLS, f"tool roster drift: {names ^ EXPECTED_TOOLS}"


async def test_registered_tools_match_capabilities_tools() -> None:
    names = {t.name for t in await _registered_tools()}
    assert names == set(TOOLS), f"capabilities.TOOLS drift: {names ^ set(TOOLS)}"


async def test_every_tool_has_a_domain_tag() -> None:
    tools = await _registered_tools()
    untagged = [t.name for t in tools if not getattr(t, "tags", None)]
    assert not untagged, f"tools missing a domain tag: {untagged}"
