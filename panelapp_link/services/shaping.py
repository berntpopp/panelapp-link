"""Response shaping for token efficiency.

Pure functions that trim the plain dict rows returned by
``panelapp_link.data.repository`` down to a ``response_mode``. The service layer
composes these into payloads; the MCP tool layer wraps those payloads in the
success/error envelope. Keeping shaping here (and pure) makes the verbosity
contract testable in isolation, per spec §7.

Verbosity contract (panels):
    minimal  - panel_id, name, region, n_genes/n_regions/n_strs
    compact  - + version, disease_group, disease_sub_group, status,
               signed_off_version/date, relevant_disorders
    standard - + version_created, description, types, entity_counts
    full     - the full repository row, untrimmed

Verbosity contract (entities):
    minimal  - entity_name, entity_type, gene_symbol, confidence_label
    compact  - + hgnc_id, confidence_level, mode_of_inheritance
    standard - + penetrance, phenotypes, extra (coords/repeats)
    full     - + evidence, publications, omim, tags (and the standard extras)
"""

from __future__ import annotations

from typing import Any

from panelapp_link.models.enums import ResponseMode


def shape_panel(row: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    """Shape a panel row (summary or detail) for a response mode.

    Args:
        row: A panel dict row from the repository (``number_of_*`` counts,
            decoded ``relevant_disorders``/``types``, optional ``entity_counts``).
        mode: One of ``minimal``/``compact``/``standard``/``full``.

    Returns:
        A JSON-ready dict trimmed to the mode.
    """
    if mode == "full":
        return dict(row)

    out: dict[str, Any] = {
        "panel_id": row.get("panel_id"),
        "name": row.get("name"),
        "region": row.get("region"),
        "n_genes": row.get("number_of_genes", 0),
        "n_regions": row.get("number_of_regions", 0),
        "n_strs": row.get("number_of_strs", 0),
    }
    if mode == "minimal":
        return out

    out.update(
        {
            "version": row.get("version"),
            "disease_group": row.get("disease_group"),
            "disease_sub_group": row.get("disease_sub_group"),
            "status": row.get("status"),
            "signed_off_version": row.get("signed_off_version"),
            "signed_off_date": row.get("signed_off_date"),
            "relevant_disorders": row.get("relevant_disorders", []),
        }
    )
    if mode == "compact":
        return out

    # standard
    out.update(
        {
            "version_created": row.get("version_created"),
            "description": row.get("description"),
            "types": row.get("types", []),
            "entity_counts": row.get("entity_counts", {}),
        }
    )
    return out


def shape_entity(row: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    """Shape a panel entity row (gene/region/str) for a response mode.

    Args:
        row: An entity dict row from the repository (decoded list columns and
            ``extra`` object already present).
        mode: One of ``minimal``/``compact``/``standard``/``full``.

    Returns:
        A JSON-ready dict trimmed to the mode.
    """
    out: dict[str, Any] = {
        "entity_name": row.get("entity_name"),
        "entity_type": row.get("entity_type"),
        "gene_symbol": row.get("gene_symbol"),
        "confidence_label": row.get("confidence_label"),
    }
    if mode == "minimal":
        return out

    out.update(
        {
            "hgnc_id": row.get("hgnc_id"),
            "confidence_level": row.get("confidence_level"),
            "mode_of_inheritance": row.get("mode_of_inheritance"),
        }
    )
    if mode == "compact":
        return out

    out.update(
        {
            "penetrance": row.get("penetrance"),
            "phenotypes": row.get("phenotypes", []),
            "extra": row.get("extra", {}),
        }
    )
    if mode == "standard":
        return out

    # full
    out.update(
        {
            "evidence": row.get("evidence", []),
            "publications": row.get("publications", []),
            "omim": row.get("omim", []),
            "tags": row.get("tags", []),
        }
    )
    return out


def shape_gene_panel_hit(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one panel a gene appears on (a ``GenePanelHit`` row).

    The internal ``confidence_rank`` ordering column is dropped; callers sort by
    it before shaping.
    """
    return {
        "region": row.get("region"),
        "panel_id": row.get("panel_id"),
        "panel_name": row.get("panel_name"),
        "confidence_label": row.get("confidence_label"),
        "confidence_level": row.get("confidence_level"),
        "mode_of_inheritance": row.get("mode_of_inheritance"),
    }


def shape_gene(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a rolled-up gene row (a ``GeneSummary``) for output.

    Drops internal columns (``gene_symbol_upper``, ``max_confidence_rank``).
    """
    return {
        "gene_symbol": row.get("gene_symbol"),
        "hgnc_id": row.get("hgnc_id"),
        "panel_count": row.get("panel_count", 0),
        "regions": row.get("regions", []),
        "max_confidence_label": row.get("max_confidence_label"),
    }
