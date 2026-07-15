"""Gene tools: get_gene_panels and resolve_gene."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_get_gene_panels, after_resolve_gene
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
# PanelApp is queried by ``entity_name`` (the gene SYMBOL): an hgnc id alone cannot
# drive the query, and the service rejects hgnc-only input. So gene_symbol is REQUIRED
# in the schema -- advertising it as optional made `get_gene_panels(hgnc_id=...)`
# schema-valid and then runtime-rejected, the same schema-vs-runtime divergence this
# module's region enum had. hgnc_id remains an OPTIONAL filter over the hits.
_GENE_SYMBOL = Annotated[
    str,
    Field(
        description="Approved gene symbol (e.g. PKD1). Required: PanelApp queries by symbol.",
        examples=["SCN1A"],
    ),
]
_HGNC_FILTER = Annotated[
    str | None,
    Field(
        description=(
            "HGNC CURIE (e.g. HGNC:1100). OPTIONAL filter over the hits -- it cannot "
            "stand alone as a query; pass gene_symbol."
        )
    ),
]
# resolve_gene's single lookup key: an approved symbol or free text. Required (so the
# schema expresses that a value is needed and the behaviour gate can build a call) and
# example-carrying (S2). An HGNC CURIE is rejected -- it is not a lookup key here.
_RESOLVE_QUERY = Annotated[
    str,
    Field(
        description=(
            "Approved gene symbol or free text to resolve to one rolled-up gene "
            "(e.g. SCN1A). An HGNC id is not a lookup key here."
        ),
        examples=["SCN1A"],
    ),
]


def register_gene_tools(mcp: FastMCP) -> None:
    """Register gene-category tools (get_gene_panels, resolve_gene)."""

    @mcp.tool(
        name="get_gene_panels",
        title="Get Panels for a Gene",
        annotations=READ_ONLY_OPEN_WORLD,
        # Tool-Surface Budget v1: no model reads outputSchema; a dict return still
        # emits structuredContent, so suppressing it is free.
        output_schema=None,
        tags={"gene"},
        description=(
            "Return every panel a gene appears on across regions, sorted by "
            "confidence. Query by gene_symbol (required); hgnc_id is an OPTIONAL "
            "result filter, not a standalone query."
        ),
    )
    async def get_gene_panels(
        gene_symbol: _GENE_SYMBOL,
        hgnc_id: _HGNC_FILTER = None,
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
        output_schema=None,
        tags={"gene", "search"},
        description=(
            "Resolve free text or an approved symbol to a single rolled-up PanelApp "
            "gene. The gene reports its symbol, hgnc id, panel count, regions, and "
            "max_confidence_label (the strongest traffic-light label across panels); "
            "matches[] always holds exactly that one gene. Pass query (an approved "
            "symbol or free text). PanelApp indexes genes by symbol, so an HGNC id is "
            "not a lookup key here. region (uk|australia|both, default both) scopes the "
            "lookup. Follow up with get_gene_panels to list the panels it appears on."
        ),
    )
    async def resolve_gene(
        query: _RESOLVE_QUERY,
        region: _REGION = "both",
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().resolve_gene(
                query=query,
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
                arguments={"query": query},
            ),
            response_mode=response_mode,
        )
