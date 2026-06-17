"""MCP facade for PanelApp-Link."""

from __future__ import annotations

from fastmcp import FastMCP

from panelapp_link.config import settings
from panelapp_link.mcp.capabilities import register_capability_resources
from panelapp_link.mcp.middleware import InputValidationMiddleware
from panelapp_link.mcp.rate_limit import RateLimitMiddleware
from panelapp_link.mcp.resources import PANELAPP_SERVER_INSTRUCTIONS
from panelapp_link.mcp.tools import register_all_tools


def create_panelapp_mcp() -> FastMCP:
    """Build a FastMCP instance for PanelApp-Link with all tools and resources."""
    mcp = FastMCP(
        name="panelapp-link",
        instructions=PANELAPP_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )
    # Rate limiting (when enabled) is outermost so an over-cap call is rejected
    # before any argument validation or upstream work happens.
    if settings.mcp_rate_limit_per_minute > 0:
        mcp.add_middleware(RateLimitMiddleware(settings.mcp_rate_limit_per_minute))
    # Error-handling middleware wraps every tool call and turns pre-body
    # argument-validation failures into a structured envelope.
    mcp.add_middleware(InputValidationMiddleware())

    register_all_tools(mcp)
    register_capability_resources(mcp)

    return mcp
