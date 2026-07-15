"""Panel tools: search_panels, get_panel, get_panel_genes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_get_panel, after_search_panels, cmd
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
# is rejected by the schema, not after a round trip through the service. Required, so
# it carries an example (Tool-Schema Documentation Standard S2).
_REGION_CONCRETE = Annotated[
    RegionConcrete,
    Field(
        description="uk (Genomics England) | australia. Panel ids are per-region; not 'both'.",
        examples=["uk"],
    ),
]
# Required positive panel id; the example lets the behaviour gate build a valid call.
_PANEL_ID = Annotated[
    int,
    Field(
        ge=1,
        description="PanelApp panel id (region-scoped, positive integer, e.g. 285).",
        examples=[285],
    ),
]
_QUERY = Annotated[
    str,
    Field(
        description=(
            "Free-text search over panel name, relevant disorders, and disease "
            "group (word-prefix match; empty returns all)."
        ),
    ),
]
_LIMIT = Annotated[int, Field(ge=1, le=500, description="Max results per page (1-500).")]
_OFFSET = Annotated[
    int,
    Field(ge=0, description="0-based offset into the result set; prefer truncated.next_cursor."),
]
_CURSOR = Annotated[
    str | None,
    Field(
        description=(
            "Opaque page token from a prior truncated.next_cursor; pass it to "
            "continue paging (rejected as invalid_input only if malformed)."
        ),
    ),
]
_ENTITY_TYPE = Annotated[
    EntityType,
    Field(description="gene (default) | region | str | all.", examples=["gene"]),
]
_MIN_CONFIDENCE = Annotated[
    ConfidenceLabel | None,
    Field(description="green | amber | red rank floor; default no filter."),
]


def register_panel_tools(mcp: FastMCP) -> None:
    """Register panel-category tools (search_panels, get_panel, get_panel_genes)."""

    @mcp.tool(
        name="search_panels",
        title="Search PanelApp Panels",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"panel", "search"},
        description=(
            "Search PanelApp panels by name, relevant disorders, or disease group "
            "across UK + Australia (region='both' default), deduped and ranked. Use "
            "it to find a panel_id, then page via _meta.next_commands."
        ),
    )
    async def search_panels(
        query: _QUERY = "",
        region: _REGION = "both",
        response_mode: _MODE = "compact",
        limit: _LIMIT = 20,
        offset: _OFFSET = 0,
        cursor: _CURSOR = None,
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
        output_schema=None,
        tags={"panel"},
        description=(
            "Return one panel's detail plus its entity-count breakdown. region must "
            "be a single concrete region ('uk' or 'australia'), not 'both'."
        ),
    )
    async def get_panel(
        panel_id: _PANEL_ID,
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
        output_schema=None,
        tags={"panel", "gene"},
        description=(
            "Return a panel's entities (genes by default; or region | str | all), "
            "filtered by min_confidence (green = green only; amber = amber+green; "
            "red = all). region must be concrete; widen response_mode for "
            "phenotypes/evidence."
        ),
    )
    async def get_panel_genes(
        panel_id: _PANEL_ID,
        region: _REGION_CONCRETE,
        entity_type: _ENTITY_TYPE = "gene",
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
        limit: _LIMIT = 100,
        offset: _OFFSET = 0,
        cursor: _CURSOR = None,
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
