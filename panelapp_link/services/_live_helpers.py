"""Pure helpers for the live PanelApp service.

Stateless functions that filter/select/reduce raw PanelApp payloads. Kept out of
``panelapp_service`` to keep that module within its line budget and to make these
small transforms independently testable.
"""

from __future__ import annotations

from typing import Any

from panelapp_link.constants import CONFIDENCE_RANK, confidence_label


def as_str(value: Any) -> str | None:
    """Cast a value to ``str`` (PanelApp versions/levels arrive as int or str)."""
    return None if value is None else str(value)


def confidence(level: Any) -> tuple[str | None, str | None, int | None]:
    """Return ``(level_str, label, rank)`` for a raw confidence_level value."""
    level_str = as_str(level)
    if level_str is None:
        return None, None, None
    label = confidence_label(level_str)
    return level_str, label, CONFIDENCE_RANK.get(label)


def panel_matches(panel: dict[str, Any], needle: str) -> bool:
    """Case-insensitive substring match over searchable panel summary fields."""
    haystacks = [
        panel.get("name") or "",
        panel.get("disease_group") or "",
        panel.get("disease_sub_group") or "",
        *(panel.get("relevant_disorders") or []),
    ]
    return any(needle in str(value).lower() for value in haystacks)


def select_entities(detail: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    """Select the raw entity list(s) for ``entity_type`` from a panel detail."""
    if entity_type == "all":
        return [
            *(detail.get("genes") or []),
            *(detail.get("regions") or []),
            *(detail.get("strs") or []),
        ]
    key = {"gene": "genes", "region": "regions", "str": "strs"}[entity_type]
    return list(detail.get(key) or [])


def results_to_hits(results: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Reduce ``/genes/`` results to ranked hits (for gene-identity roll-up)."""
    hits: list[dict[str, Any]] = []
    for region_key, result in results:
        _level, label, rank = confidence(result.get("confidence_level"))
        hits.append({"region": region_key, "confidence_label": label, "confidence_rank": rank})
    return hits


def gene_identity(
    symbol: str,
    results: list[tuple[str, dict[str, Any]]],
    hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Roll a gene identity (symbol, hgnc id, panel count, regions, max label) up."""
    gene_symbol = symbol.upper()
    hgnc_id: str | None = None
    for _region_key, result in results:
        gene_data = result.get("gene_data") or {}
        if gene_data.get("gene_symbol"):
            gene_symbol = gene_data["gene_symbol"]
        if hgnc_id is None and gene_data.get("hgnc_id"):
            hgnc_id = gene_data["hgnc_id"]

    regions = sorted({region_key for region_key, _ in results})
    max_rank = 0
    max_label: str | None = None
    for hit in hits:
        rank = hit.get("confidence_rank") or 0
        if rank > max_rank:
            max_rank = rank
            max_label = hit.get("confidence_label")
    return {
        "gene_symbol": gene_symbol,
        "hgnc_id": hgnc_id,
        "panel_count": len(results),
        "regions": regions,
        "max_confidence_label": max_label,
    }
