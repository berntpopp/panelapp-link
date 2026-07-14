"""Panel tools: search_panels, get_panel, get_panel_genes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_get_panel, after_search_panels, cmd
from panelapp_link.mcp.schemas import (
    GET_PANEL_GENES_SCHEMA,
    GET_PANEL_SCHEMA,
    SEARCH_PANELS_SCHEMA,
)
from panelapp_link.mcp.service_adapters import get_panelapp_service
from panelapp_link.models.enums import (
    ConfidenceLabel,
    EntityType,
    Region,
    RegionConcrete,
    ResponseMode,
)

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
# Panel ids are per-region, so the advertised enum is ("uk", "australia") -- 'both'
# is rejected by the schema, not after a round trip through the service.
_REGION_CONCRETE = Annotated[
    RegionConcrete,
    Field(description="uk (Genomics England) | australia. Panel ids are per-region; not 'both'."),
]


def register_panel_tools(mcp: FastMCP) -> None:
    """Register panel-category tools (search_panels, get_panel, get_panel_genes)."""

    @mcp.tool(
        name="search_panels",
        title="Search PanelApp Panels",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=SEARCH_PANELS_SCHEMA,
        tags={"panel", "search"},
        description=(
            "Search PanelApp panels by name, relevant disorders, or disease group "
            "across UK + Australia (region='both' default), deduped and ranked. Use "
            "it to find a panel_id, then page via _meta.next_commands."
        ),
    )
    async def search_panels(
        query: str = "",
        region: _REGION = "both",
        response_mode: _MODE = "compact",
        limit: int = 20,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().search_panels(
                query,
                region=region,
                response_mode=response_mode,
                limit=limit,
                offset=offset,
                cursor=cursor,
            )
            nexts: list[dict[str, Any]] = []
            trunc = payload.get("truncated") or {}
            if trunc.get("next_cursor"):
                # Page-forward first so an agent sweeping next_commands[0] walks
                # the full result set.
                nexts.append(cmd("search_panels", region=region, cursor=trunc["next_cursor"]))
            nexts.extend(after_search_panels(payload.get("panels", [])))
            payload["_meta"] = {"next_commands": nexts[:5]}
            return payload

        return await run_mcp_tool(
            "search_panels",
            call,
            context=McpErrorContext("search_panels", arguments={"query": query, "region": region}),
            response_mode=response_mode,
        )

    @mcp.tool(
        name="get_panel",
        title="Get PanelApp Panel",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=GET_PANEL_SCHEMA,
        tags={"panel"},
        description=(
            "Return one panel's detail plus its entity-count breakdown. region must "
            "be a single concrete region ('uk' or 'australia'), not 'both'."
        ),
    )
    async def get_panel(
        panel_id: int,
        region: _REGION_CONCRETE,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().get_panel(
                panel_id, region, response_mode=response_mode
            )
            payload["_meta"] = {"next_commands": after_get_panel(region, panel_id)}
            return payload

        return await run_mcp_tool(
            "get_panel",
            call,
            context=McpErrorContext(
                "get_panel", arguments={"panel_id": panel_id, "region": region}
            ),
            response_mode=response_mode,
        )

    @mcp.tool(
        name="get_panel_genes",
        title="Get PanelApp Panel Entities",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=GET_PANEL_GENES_SCHEMA,
        tags={"panel", "gene"},
        description=(
            "Return a panel's entities (genes by default; or region | str | all), "
            "filtered by min_confidence (green = green only; amber = amber+green; "
            "red = all). region must be concrete; widen response_mode for "
            "phenotypes/evidence."
        ),
    )
    async def get_panel_genes(
        panel_id: int,
        region: _REGION_CONCRETE,
        entity_type: Annotated[
            EntityType,
            Field(description="gene (default) | region | str | all."),
        ] = "gene",
        min_confidence: Annotated[
            ConfidenceLabel | None,
            Field(description="green | amber | red rank floor; default no filter."),
        ] = None,
        response_mode: _MODE = "compact",
        limit: int = 100,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_panelapp_service().get_panel_genes(
                panel_id,
                region,
                entity_type=entity_type,
                min_confidence=min_confidence,
                response_mode=response_mode,
                limit=limit,
                offset=offset,
                cursor=cursor,
            )
            nexts: list[dict[str, Any]] = []
            trunc = payload.get("truncated") or {}
            if trunc.get("next_cursor"):
                nexts.append(
                    cmd(
                        "get_panel_genes",
                        panel_id=panel_id,
                        region=region,
                        entity_type=entity_type,
                        cursor=trunc["next_cursor"],
                    )
                )
            payload["_meta"] = {"next_commands": nexts[:5]}
            return payload

        return await run_mcp_tool(
            "get_panel_genes",
            call,
            context=McpErrorContext(
                "get_panel_genes", arguments={"panel_id": panel_id, "region": region}
            ),
            response_mode=response_mode,
        )
