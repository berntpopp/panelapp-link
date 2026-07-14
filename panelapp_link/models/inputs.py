"""Typed MCP tool-input models.

FastMCP derives a tool's advertised JSON Schema from its signature, so an input
model *is* the contract an agent reads before it calls. A freeform
``dict[str, Any]`` advertises nothing, gives the caller no guidance, and pushes
rejection to the runtime -- one wasted round trip per mistake.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from panelapp_link.models.enums import RegionConcrete


class PanelRef(BaseModel):
    """One compare_panels panel reference: a panel id plus its region.

    PanelApp panel ids are per-region (UK panel 285 is a different panel from
    Australia panel 285), so each ref carries its own concrete region -- 'both' is
    not a namespace a panel id can live in.

    This docstring is advertised verbatim as the JSON-Schema description of a
    panels[] item; keep it plain prose an agent can act on.
    """

    model_config = ConfigDict(extra="forbid")

    panel_id: int = Field(description="PanelApp panel id (region-scoped).")
    region: RegionConcrete = Field(description="uk (Genomics England) | australia.")
