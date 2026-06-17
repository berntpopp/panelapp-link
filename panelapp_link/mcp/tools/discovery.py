"""Discovery tools: server capabilities and data diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.capabilities import build_capabilities
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.schemas import CAPABILITIES_SCHEMA, DIAGNOSTICS_SCHEMA
from panelapp_link.mcp.service_adapters import get_panelapp_service

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register get_server_capabilities and get_panelapp_diagnostics."""

    @mcp.tool(
        name="get_server_capabilities",
        title="Get Server Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=CAPABILITIES_SCHEMA,
        tags={"discovery"},
        description=(
            "Return the PanelApp-Link tool inventory, vocabulary (confidence labels "
            "and ranks, entity types, regions), response modes, recommended "
            "workflows, error codes, resources, and live data freshness. Compare "
            "`capabilities_version` to skip re-fetching when unchanged."
        ),
    )
    async def get_server_capabilities() -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            return build_capabilities()

        return await run_mcp_tool(
            "get_server_capabilities",
            call,
            context=McpErrorContext("get_server_capabilities"),
        )

    @mcp.tool(
        name="get_panelapp_diagnostics",
        title="Get PanelApp Diagnostics",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=DIAGNOSTICS_SCHEMA,
        tags={"discovery"},
        description=(
            "Report live backend status: the data mode (live), the upstream "
            "PanelApp source URLs (UK + Australia), the in-memory cache TTL, "
            "current cache stats, and the RED metrics snapshot (request/error "
            "counts, cache hit ratio, tool + per-region upstream duration "
            "p50/p95/p99 -- also exported as Prometheus text at GET /metrics). "
            "Also echoes server_version and capabilities_version so a warm client "
            "can poll this small payload for drift instead of re-fetching full "
            "capabilities."
        ),
    )
    async def get_panelapp_diagnostics() -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            from panelapp_link.mcp.capabilities import capabilities_version, server_version

            data = get_panelapp_service().diagnostics()
            sources = data.get("sources", {})
            headline = (
                f"PanelApp live backend: UK {sources.get('uk', '?')}, "
                f"AU {sources.get('australia', '?')}; "
                f"cache TTL {data.get('cache_ttl_seconds', 0)}s."
            )
            return {
                "headline": headline,
                "server_version": server_version(),
                "capabilities_version": capabilities_version(),
                "data": data,
            }

        return await run_mcp_tool(
            "get_panelapp_diagnostics",
            call,
            context=McpErrorContext("get_panelapp_diagnostics"),
        )
