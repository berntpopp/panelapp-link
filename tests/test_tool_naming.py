"""Tool-naming guard: every registered MCP tool is snake_case and on the roster.

W7 registers the tools directly on a bare ``FastMCP`` (the W9 facade does not
exist yet), so these tests build a fresh instance, register everything, and list
the live tool objects. Names must be snake_case (``^[a-z][a-z0-9_]*$``) and equal
the fleet-frozen expected set.
"""

from __future__ import annotations

import re

import pytest
from fastmcp import FastMCP

from panelapp_link.mcp.capabilities import TOOLS
from panelapp_link.mcp.tools import register_all_tools

pytestmark = pytest.mark.mcp

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

EXPECTED_TOOLS = {
    "search_panels",
    "get_panel",
    "get_panel_genes",
    "get_gene_panels",
    "resolve_gene",
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
