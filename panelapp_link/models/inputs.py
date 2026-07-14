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

    # extra="ignore" (NOT "forbid"): the service accepted a ref carrying extra keys and
    # stripped it to {panel_id, region}, and an agent will very plausibly hand a whole
    # panel row from a search_panels / get_panel result straight back to compare_panels.
    # Forbidding extras would break that -- and would ALSO be the same schema-vs-runtime
    # divergence in reverse, since pydantic advertises additionalProperties:false only
    # for "forbid". Ignoring them keeps the advertised schema and the runtime identical.
    #
    # panel_id stays a plain `int`, not StrictInt: pydantic coerces "1207" -> 1207, so
    # the runtime is *more* permissive than the advertised `type: integer`. That is the
    # safe direction -- a caller that obeys the schema always succeeds -- and LLM callers
    # do emit stringified integers.
    model_config = ConfigDict(extra="ignore")

    panel_id: int = Field(description="PanelApp panel id (region-scoped).")
    region: RegionConcrete = Field(description="uk (Genomics England) | australia.")
