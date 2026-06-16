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
            "Report build provenance and data freshness from the local database: "
            "schema version, per-region panel counts, entity/gene counts, source "
            "URLs, and when the database was built. Returns a data_unavailable "
            "envelope when the database has not been built. Also echoes "
            "server_version and capabilities_version so a warm client can poll this "
            "small payload for drift instead of re-fetching full capabilities."
        ),
    )
    async def get_panelapp_diagnostics() -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            from panelapp_link.config import get_data_config
            from panelapp_link.mcp.capabilities import capabilities_version, server_version
            from panelapp_link.services.refresh import get_active_scheduler

            meta = get_panelapp_service().diagnostics()
            cfg = get_data_config()
            scheduler = get_active_scheduler()
            refresh: dict[str, Any] = {
                "enabled": cfg.refresh_enabled,
                "interval_hours": cfg.refresh_interval_hours,
                "scheduler_running": scheduler is not None,
            }
            if scheduler is not None:
                refresh["status"] = scheduler.status
            headline = (
                f"PanelApp data: {meta.get('uk_panel_count', 0)} UK + "
                f"{meta.get('au_panel_count', 0)} AU panels, "
                f"{meta.get('entity_count', 0)} entities, {meta.get('gene_count', 0)} genes; "
                f"built {meta.get('build_utc') or 'unknown'}."
            )
            return {
                "headline": headline,
                "server_version": server_version(),
                "capabilities_version": capabilities_version(),
                "data": meta,
                "refresh": refresh,
            }

        return await run_mcp_tool(
            "get_panelapp_diagnostics",
            call,
            context=McpErrorContext("get_panelapp_diagnostics"),
        )
