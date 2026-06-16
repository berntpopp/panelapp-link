"""Response shaping + live-payload normalization for token efficiency.

Pure functions in two layers:

1. ``normalize_panel`` / ``normalize_entity`` flatten a raw PanelApp REST payload
   (panel summary/detail, or a gene/region/str entity) into the flat dict shape
   the shapers consume. This keeps the live-API field names in one place.
2. ``shape_panel`` / ``shape_entity`` / ``shape_gene_panel_hit`` / ``shape_gene``
   trim those flat dicts down to a ``response_mode``.

The service layer composes these into payloads; the MCP tool layer wraps those
payloads in the success/error envelope. Keeping shaping here (and pure) makes the
verbosity contract testable in isolation, per spec §7.

Verbosity contract (panels):
    minimal  - panel_id, name, region, n_genes/n_regions/n_strs
    compact  - + version, disease_group, disease_sub_group, status,
               signed_off_version/date, relevant_disorders
    standard - + version_created, description, types, entity_counts
    full     - the full normalized row, untrimmed

Verbosity contract (entities):
    minimal  - entity_name, entity_type, gene_symbol, confidence_label
    compact  - + hgnc_id, confidence_level, mode_of_inheritance
    standard - + penetrance, phenotypes, extra (coords/repeats)
    full     - + evidence, publications, omim, tags (and the standard extras)
"""

from __future__ import annotations

from typing import Any

from panelapp_link.constants import CONFIDENCE_RANK, confidence_label
from panelapp_link.models.enums import ResponseMode

# gene_data sub-fields packed into region/str ``extra``.
_REGION_EXTRA_FIELDS = (
    "chromosome",
    "grch37_coordinates",
    "grch38_coordinates",
    "haploinsufficiency_score",
    "triplosensitivity_score",
    "type_of_variants",
    "verbose_name",
    "required_overlap_percentage",
)
_STR_EXTRA_FIELDS = (
    "repeated_sequence",
    "normal_repeats",
    "pathogenic_repeats",
    "chromosome",
    "grch37_coordinates",
    "grch38_coordinates",
)


def _as_str_or_none(value: Any) -> str | None:
    """Cast a value to ``str`` (PanelApp versions/levels arrive as int or str)."""
    return None if value is None else str(value)


def normalize_panel(
    live: dict[str, Any],
    region: str,
    signed_off: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten a raw PanelApp panel (summary or detail) into a shaper-ready dict.

    Args:
        live: A panel summary or detail object straight from the REST API.
        region: The region key this panel was fetched from (``uk``/``australia``).
        signed_off: Optional ``{"version", "signed_off"}`` annotation for this id.

    Returns:
        A flat dict with the keys ``shape_panel`` expects. When the live payload
        carries ``genes``/``regions``/``strs`` (detail), an ``entity_counts``
        breakdown is included.
    """
    stats = live.get("stats") or {}
    signed = signed_off or {}
    out: dict[str, Any] = {
        "region": region,
        "panel_id": int(live["id"]) if live.get("id") is not None else None,
        "hash_id": live.get("hash_id"),
        "name": live.get("name") or "",
        "version": _as_str_or_none(live.get("version")),
        "version_created": live.get("version_created"),
        "disease_group": live.get("disease_group"),
        "disease_sub_group": live.get("disease_sub_group"),
        "status": live.get("status"),
        "description": live.get("description"),
        "relevant_disorders": live.get("relevant_disorders") or [],
        "types": live.get("types") or [],
        "number_of_genes": int(stats.get("number_of_genes") or 0),
        "number_of_regions": int(stats.get("number_of_regions") or 0),
        "number_of_strs": int(stats.get("number_of_strs") or 0),
        "signed_off_version": _as_str_or_none(signed.get("version")),
        "signed_off_date": signed.get("signed_off"),
    }
    if any(key in live for key in ("genes", "regions", "strs")):
        out["entity_counts"] = {
            "gene": len(live.get("genes") or []),
            "region": len(live.get("regions") or []),
            "str": len(live.get("strs") or []),
        }
    return out


def normalize_entity(
    live: dict[str, Any],
    region: str,
    panel_id: int,
    panel_name: str,
) -> dict[str, Any]:
    """Flatten a raw PanelApp entity (gene/region/str) into a shaper-ready dict.

    Args:
        live: A gene/region/str entity object from a panel detail or ``/genes/``.
        region: The region key this entity was fetched from.
        panel_id: The owning panel id.
        panel_name: The owning panel name (denormalized for gene->panels).

    Returns:
        A flat dict with the keys ``shape_entity`` expects, plus internal
        ``confidence_rank``/``panel_id``/``region``/``panel_name`` fields the
        service uses for filtering and gene-hit shaping.
    """
    entity_type = live.get("entity_type") or "gene"
    gene_data = live.get("gene_data") or {}
    gene_symbol = gene_data.get("gene_symbol")
    level = _as_str_or_none(live.get("confidence_level"))
    label = confidence_label(level) if level is not None else None
    rank = CONFIDENCE_RANK.get(label) if label is not None else None

    if entity_type == "region":
        extra = {f: live.get(f) for f in _REGION_EXTRA_FIELDS if live.get(f) not in (None, "")}
    elif entity_type == "str":
        extra = {f: live.get(f) for f in _STR_EXTRA_FIELDS if live.get(f) not in (None, "")}
    else:
        extra = {}

    return {
        "region": region,
        "panel_id": panel_id,
        "panel_name": panel_name,
        "entity_type": entity_type,
        "entity_name": live.get("entity_name") or "",
        "gene_symbol": gene_symbol,
        "gene_symbol_upper": gene_symbol.upper() if gene_symbol else None,
        "hgnc_id": gene_data.get("hgnc_id"),
        "confidence_level": level,
        "confidence_label": label,
        "confidence_rank": rank,
        "mode_of_inheritance": live.get("mode_of_inheritance"),
        "penetrance": live.get("penetrance"),
        "phenotypes": live.get("phenotypes") or [],
        "evidence": live.get("evidence") or [],
        "publications": live.get("publications") or [],
        "omim": gene_data.get("omim_gene") or [],
        "tags": live.get("tags") or [],
        "extra": extra,
    }


def shape_panel(row: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    """Shape a normalized panel row for a response mode.

    Args:
        row: A normalized panel dict (``number_of_*`` counts, ``relevant_disorders``,
            ``types``, optional ``entity_counts``).
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
    """Shape a normalized panel entity row (gene/region/str) for a response mode.

    Args:
        row: A normalized entity dict (decoded list columns and ``extra`` object).
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
        "version": row.get("version"),
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
