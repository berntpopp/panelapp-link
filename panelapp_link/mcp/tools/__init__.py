"""MCP tool registration for PanelApp-Link."""

from __future__ import annotations

from typing import TYPE_CHECKING

from panelapp_link.mcp.tools.discovery import register_discovery_tools
from panelapp_link.mcp.tools.genes import register_gene_tools
from panelapp_link.mcp.tools.panels import register_panel_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = [
    "register_all_tools",
    "register_discovery_tools",
    "register_gene_tools",
    "register_panel_tools",
]


def register_all_tools(mcp: FastMCP) -> None:
    """Register every PanelApp-Link tool (panels, genes, discovery) on ``mcp``."""
    register_panel_tools(mcp)
    register_gene_tools(mcp)
    register_discovery_tools(mcp)
