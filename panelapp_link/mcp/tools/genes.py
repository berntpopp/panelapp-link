"""Gene tools: get_gene_panels and resolve_gene."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_get_gene_panels, after_resolve_gene
from panelapp_link.mcp.schemas import GET_GENE_PANELS_SCHEMA, RESOLVE_GENE_SCHEMA
from panelapp_link.mcp.service_adapters import get_panelapp_service
from panelapp_link.models.enums import ConfidenceLabel, Region, ResponseMode

if TYPE_CHECKING:
    from fastmcp import FastMCP

_MODE = Annotated[
    ResponseMode,
    Field(description="Verbosity: minimal | compact | standard | full (default compact)."),
]
_REGION = Annotated[
    Region,
    Field(description="uk (Genomics England) | australia | both (default)."),
]
_MIN_CONFIDENCE = Annotated[
    ConfidenceLabel | None,
    Field(description="green | amber | red rank floor; default no filter."),
]


def register_gene_tools(mcp: FastMCP) -> None:
    """Register gene-category tools (get_gene_panels, resolve_gene)."""

    @mcp.tool(
        name="get_gene_panels",
        title="Get Panels for a Gene",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=GET_GENE_PANELS_SCHEMA,
        tags={"gene"},
        description=(
            "Return every PanelApp panel a gene appears on, across regions, sorted "
            "by confidence (green > amber > red) then region. Identify the gene with "
            "EITHER gene_symbol (approved symbol, e.g. BRCA1) OR hgnc_id (HGNC CURIE, "
            "e.g. HGNC:1100). region='both' (default) spans UK + Australia; "
            "min_confidence floors the traffic-light rank (green = green only). Use "
            "resolve_gene first if a free-text symbol is uncertain."
        ),
    )
    async def get_gene_panels(
        gene_symbol: str | None = None,
        hgnc_id: str | None = None,
        region: _REGION = "both",
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().get_gene_panels(
                gene_symbol=gene_symbol,
                hgnc_id=hgnc_id,
                region=region,
                min_confidence=min_confidence,
                response_mode=response_mode,
            )
            payload["_meta"] = {"next_commands": after_get_gene_panels(payload.get("panels", []))}
            return payload

        return await run_mcp_tool(
            "get_gene_panels",
            call,
            context=McpErrorContext(
                "get_gene_panels",
                arguments={"gene_symbol": gene_symbol, "hgnc_id": hgnc_id},
            ),
            response_mode=response_mode,
        )

    @mcp.tool(
        name="resolve_gene",
        title="Resolve a Gene",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=RESOLVE_GENE_SCHEMA,
        tags={"gene", "search"},
        description=(
            "Resolve free text, an approved symbol, or an HGNC CURIE to a single "
            "rolled-up PanelApp gene (symbol, hgnc id, panel count, regions, strongest "
            "confidence). Pass one of query, gene_symbol, or hgnc_id. region "
            "(uk|australia|both, default both) scopes the lookup. Returns "
            "ambiguous_query when an id maps to multiple symbols. Follow up with "
            "get_gene_panels to list the panels the gene appears on."
        ),
    )
    async def resolve_gene(
        query: str | None = None,
        gene_symbol: str | None = None,
        hgnc_id: str | None = None,
        region: _REGION = "both",
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().resolve_gene(
                query=query,
                gene_symbol=gene_symbol,
                hgnc_id=hgnc_id,
                region=region,
                response_mode=response_mode,
            )
            payload["_meta"] = {"next_commands": after_resolve_gene(payload.get("gene", {}))}
            return payload

        return await run_mcp_tool(
            "resolve_gene",
            call,
            context=McpErrorContext(
                "resolve_gene",
                arguments={
                    "query": query,
                    "gene_symbol": gene_symbol,
                    "hgnc_id": hgnc_id,
                },
            ),
            response_mode=response_mode,
        )
