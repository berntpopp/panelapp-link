"""MCP facade for PanelApp-Link."""

from __future__ import annotations

from fastmcp import FastMCP

from panelapp_link.mcp.capabilities import register_capability_resources
from panelapp_link.mcp.middleware import InputValidationMiddleware
from panelapp_link.mcp.resources import PANELAPP_SERVER_INSTRUCTIONS
from panelapp_link.mcp.tools import register_all_tools


def create_panelapp_mcp() -> FastMCP:
    """Build a FastMCP instance for PanelApp-Link with all tools and resources."""
    mcp = FastMCP(
        name="panelapp-link",
        instructions=PANELAPP_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )
    # Error-handling middleware goes first so it wraps every tool call and can
    # turn pre-body argument-validation failures into a structured envelope.
    mcp.add_middleware(InputValidationMiddleware())

    register_all_tools(mcp)
    register_capability_resources(mcp)

    return mcp
