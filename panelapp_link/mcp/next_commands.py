"""Builders for _meta.next_commands entries: {tool, arguments} ready-to-call steps.

Every success and error envelope can carry ``_meta.next_commands`` -- a short,
capped list of ready-to-call ``{tool, arguments}`` steps so an agent advances (or
recovers) deterministically instead of guessing the next tool.
"""

from __future__ import annotations

from typing import Any

_MAX_NEXT_COMMANDS = 5


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One ready-to-call next step."""
    return {"tool": tool, "arguments": arguments}


def _panel_region(panel: dict[str, Any]) -> str | None:
    """Pull the per-panel region; ``get_panel`` requires a concrete region."""
    region = panel.get("region")
    return region if region in ("uk", "australia") else None


def after_search_panels(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After ranking panels: drill into the top few panels' detail + genes.

    Emits a ``get_panel`` then a ``get_panel_genes`` for each of the first
    panels (region-qualified), capped at ``_MAX_NEXT_COMMANDS`` total commands.
    Panels missing a concrete region (uk/australia) are skipped.
    """
    nexts: list[dict[str, Any]] = []
    for panel in panels:
        region = _panel_region(panel)
        panel_id = panel.get("id") if "id" in panel else panel.get("panel_id")
        if region is None or panel_id is None:
            continue
        nexts.append(cmd("get_panel", panel_id=panel_id, region=region))
        if len(nexts) >= _MAX_NEXT_COMMANDS:
            break
        nexts.append(cmd("get_panel_genes", panel_id=panel_id, region=region))
        if len(nexts) >= _MAX_NEXT_COMMANDS:
            break
    return nexts[:_MAX_NEXT_COMMANDS]


def after_resolve_gene(gene: dict[str, Any]) -> list[dict[str, Any]]:
    """After resolving a gene: list every panel it appears on across regions."""
    symbol = gene.get("gene_symbol")
    hgnc_id = gene.get("hgnc_id")
    if hgnc_id:
        return [cmd("get_gene_panels", hgnc_id=hgnc_id)]
    if symbol:
        return [cmd("get_gene_panels", gene_symbol=symbol)]
    return []


def after_get_panel(region: str, panel_id: int) -> list[dict[str, Any]]:
    """After a panel's detail: pull its entities (genes by default)."""
    return [cmd("get_panel_genes", panel_id=panel_id, region=region)]


def after_get_gene_panels(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After a gene's panel footprint: drill into the top panels' detail.

    Emits a ``get_panel`` for each of the first distinct (region, panel) hits,
    capped at ``_MAX_NEXT_COMMANDS``. Hits missing a concrete region/id are
    skipped.
    """
    nexts: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for panel in panels:
        region = _panel_region(panel)
        panel_id = panel.get("panel_id")
        if region is None or panel_id is None:
            continue
        key = (region, int(panel_id))
        if key in seen:
            continue
        seen.add(key)
        nexts.append(cmd("get_panel", panel_id=panel_id, region=region))
        if len(nexts) >= _MAX_NEXT_COMMANDS:
            break
    return nexts[:_MAX_NEXT_COMMANDS]


def recovery_commands(
    tool: str, error_code: str, arguments: dict[str, Any], field: str | None
) -> list[dict[str, Any]]:
    """Ready-to-call recovery steps for an error envelope (empty when none apply).

    Mirrors the success-path ``next_commands`` so an agent can deterministically
    recover from a failure instead of parsing the prose ``recovery_action``.
    Always capped at ``_MAX_NEXT_COMMANDS``.
    """
    gene_in = arguments.get("hgnc_id") or arguments.get("gene_symbol") or arguments.get("query")
    nexts: list[dict[str, Any]] = []
    if error_code == "not_found":
        if tool in ("get_panel", "get_panel_genes"):
            nexts = [cmd("search_panels", query="")]
        elif tool == "resolve_gene" and gene_in:
            nexts = [cmd("search_panels", query=gene_in)]
        elif tool == "get_gene_panels" and gene_in:
            nexts = [cmd("resolve_gene", query=gene_in)]
    elif error_code == "invalid_input":
        nexts = [cmd("get_server_capabilities")]
    return nexts[:_MAX_NEXT_COMMANDS]
