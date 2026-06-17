"""Aggregation tools: compare_panels and get_panels_for_genes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_compare_panels, after_panels_for_genes
from panelapp_link.mcp.schemas import COMPARE_PANELS_SCHEMA, GET_PANELS_FOR_GENES_SCHEMA
from panelapp_link.mcp.service_adapters import get_panelapp_service
from panelapp_link.models.enums import ConfidenceLabel, Region, ResponseMode
from panelapp_link.services import aggregations

if TYPE_CHECKING:
    from fastmcp import FastMCP

_MODE = Annotated[
    ResponseMode,
    Field(description="Verbosity: minimal | compact | standard | full (default compact)."),
]
_REGION = Annotated[Region, Field(description="uk | australia | both (default).")]
_MIN_CONFIDENCE = Annotated[
    ConfidenceLabel | None,
    Field(description="green | amber | red rank floor; default no filter."),
]
_PANELS = Annotated[
    list[dict[str, Any]],
    Field(description="2-5 panel refs: [{panel_id:int, region:'uk'|'australia'}]."),
]
_SYMBOLS = Annotated[
    list[str], Field(description="Approved gene symbols (e.g. PKD1); capped at 20 per call.")
]


def register_aggregation_tools(mcp: FastMCP) -> None:
    """Register the aggregation tools (compare_panels, get_panels_for_genes)."""

    @mcp.tool(
        name="compare_panels",
        title="Compare PanelApp Panels",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=COMPARE_PANELS_SCHEMA,
        tags={"panel", "compare"},
        description=(
            "Diff genes across 2-5 panels server-side: shared genes, genes unique to "
            "each panel, and per-panel confidence deltas. Pass concrete-region refs "
            "({panel_id, region}); 'both' is rejected. Cheaper than pulling each "
            "panel's full gene list and diffing in context."
        ),
    )
    async def compare_panels(
        panels: _PANELS,
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await aggregations.compare_panels(
                get_panelapp_service(),
                panels,
                min_confidence=min_confidence,
                response_mode=response_mode,
            )
            payload["_meta"] = {"next_commands": after_compare_panels(payload.get("panels", []))}
            return payload

        return await run_mcp_tool(
            "compare_panels",
            call,
            context=McpErrorContext("compare_panels", arguments={"panels": panels}),
            response_mode=response_mode,
        )

    @mcp.tool(
        name="get_panels_for_genes",
        title="Get Panels for Many Genes",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=GET_PANELS_FOR_GENES_SCHEMA,
        tags={"gene", "batch"},
        description=(
            "Batch gene->panel membership for up to 20 gene symbols in one call: per "
            "gene, the panel_count, max_confidence_label, and panels it appears on. "
            "Unknown symbols are returned in not_found; over-cap input is truncated."
        ),
    )
    async def get_panels_for_genes(
        gene_symbols: _SYMBOLS,
        region: _REGION = "both",
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            from panelapp_link.config import settings

            payload = await aggregations.panels_for_genes(
                get_panelapp_service(),
                gene_symbols,
                region=region,
                min_confidence=min_confidence,
                response_mode=response_mode,
                cap=settings.data.gene_batch_cap,
            )
            payload["_meta"] = {"next_commands": after_panels_for_genes(payload.get("genes", {}))}
            return payload

        return await run_mcp_tool(
            "get_panels_for_genes",
            call,
            context=McpErrorContext(
                "get_panels_for_genes", arguments={"gene_symbols": gene_symbols}
            ),
            response_mode=response_mode,
        )
