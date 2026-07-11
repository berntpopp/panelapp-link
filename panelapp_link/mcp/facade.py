"""MCP facade for PanelApp-Link."""

from __future__ import annotations

from fastmcp import FastMCP

from panelapp_link import __version__
from panelapp_link.config import settings
from panelapp_link.mcp.capabilities import register_capability_resources
from panelapp_link.mcp.log_filters import install_external_error_filter
from panelapp_link.mcp.middleware import (
    InputValidationMiddleware,
    install_protocol_error_handler,
)
from panelapp_link.mcp.rate_limit import RateLimitMiddleware
from panelapp_link.mcp.resources import PANELAPP_SERVER_INSTRUCTIONS
from panelapp_link.mcp.tools import register_all_tools


def create_panelapp_mcp() -> FastMCP:
    """Build a FastMCP instance for PanelApp-Link with all tools and resources."""
    mcp = FastMCP(
        name="panelapp-link",
        version=__version__,
        instructions=PANELAPP_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )
    # Scrub FastMCP-core / MCP-SDK log records that reflect the caller's OWN requested
    # tool name / resource URI / prompt name (unknown-tool "Handler called" DEBUG lines,
    # the root "Failed to validate request" record, arg-validation WARNINGs). Attached to
    # each SOURCE logger + FastMCP's non-propagating Rich handlers now that they exist.
    install_external_error_filter()
    # Rate limiting (when enabled) is outermost so an over-cap call is rejected
    # before any argument validation or upstream work happens.
    if settings.mcp_rate_limit_per_minute > 0:
        mcp.add_middleware(RateLimitMiddleware(settings.mcp_rate_limit_per_minute))
    # Error-handling middleware wraps every tool call: Layer-1 unknown-tool preflight +
    # arg-validation envelope (on_call_tool) and Layer-2 URI-free resource errors
    # (on_read_resource).
    mcp.add_middleware(InputValidationMiddleware())

    register_all_tools(mcp)
    register_capability_resources(mcp)

    # Layer-3 protocol backstop: wrap the raw tool/prompt request handlers as the
    # OUTERMOST guard so FastMCP core cannot reflect a caller-supplied tool name (return
    # path) or prompt name in a not-found JSON-RPC error frame. Installed last, after all
    # handlers exist.
    install_protocol_error_handler(mcp)

    return mcp
