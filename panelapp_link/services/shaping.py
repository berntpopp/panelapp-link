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
    standard - + version_created, description, types, entity_counts,
               confidence_counts (per-entity-type traffic-light tallies on detail)
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
from panelapp_link.mcp.untrusted_content import UntrustedText, fence_untrusted_text
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


def _confidence_counts(live: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per-entity-type traffic-light tallies from a panel detail payload."""
    out: dict[str, dict[str, int]] = {}
    for etype, key in (("gene", "genes"), ("region", "regions"), ("str", "strs")):
        items = live.get(key)
        if not items:
            continue
        counts = {"green": 0, "amber": 0, "red": 0}
        for item in items:
            level = _as_str_or_none(item.get("confidence_level"))
            label = confidence_label(level) if level is not None else None
            if label in counts:
                counts[label] += 1
        out[etype] = counts
    return out


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
        out["confidence_counts"] = _confidence_counts(live)
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


def _panel_record_id(region: Any, panel_id: Any) -> str:
    """Region-qualified stable panel id. PanelApp panel ids are **per-region**
    (the same integer id names different panels in UK vs Australia), so the
    region is required for a fenced record to be auditable / re-retrievable.
    """
    return f"panel:{region}:{panel_id}"


def _fence_panel_description(
    out: dict[str, Any], region: Any, panel_id: Any, fenced: list[UntrustedText] | None
) -> None:
    """Reshape ``out["description"]`` from a bare string to a v1.1 ``untrusted_text``
    object (Response-Envelope Standard v1.1). ``get_panel``/``search_panels`` both
    route through this one boundary, so a single fence covers ``/panel/description``
    and ``/panels/*/description``. A present-but-empty/non-string description is
    normalized to ``None`` so the strict output schema (object|null) stays valid.
    """
    if "description" not in out:
        return
    raw = out.get("description")
    if isinstance(raw, str) and raw:
        obj = fence_untrusted_text(
            raw, source="panelapp", record_id=_panel_record_id(region, panel_id)
        )
        if fenced is not None:
            fenced.append(obj)
        out["description"] = obj.model_dump(mode="json")
    else:
        out["description"] = None


def _fence_panel_types(
    types: Any, *, region: Any, panel_id: Any, fenced: list[UntrustedText] | None
) -> Any:
    """Fence each panel type's curator ``description`` (``/panel/types/*/description``
    and ``/panels/*/types/*/description``) as its own ``untrusted_text`` object.

    Returns NEW type dicts -- never mutates the cached upstream ``types`` list,
    which is shared by reference from the in-memory request cache.
    """
    if not isinstance(types, list):
        return types
    out: list[Any] = []
    for item in types:
        if not isinstance(item, dict):
            out.append(item)
            continue
        new_item = dict(item)
        raw = new_item.get("description")
        if isinstance(raw, str) and raw:
            slug = new_item.get("slug") or new_item.get("name") or "type"
            obj = fence_untrusted_text(
                raw,
                source="panelapp",
                record_id=f"{_panel_record_id(region, panel_id)}#type:{slug}",
            )
            if fenced is not None:
                fenced.append(obj)
            new_item["description"] = obj.model_dump(mode="json")
        elif "description" in new_item:
            new_item["description"] = None
        out.append(new_item)
    return out


def shape_panel(
    row: dict[str, Any],
    mode: ResponseMode,
    fenced: list[UntrustedText] | None = None,
) -> dict[str, Any]:
    """Shape a normalized panel row for a response mode.

    Args:
        row: A normalized panel dict (``number_of_*`` counts, ``relevant_disorders``,
            ``types``, optional ``entity_counts``).
        mode: One of ``minimal``/``compact``/``standard``/``full``.
        fenced: Optional accumulator collecting every ``UntrustedText`` object
            fenced by this call, so the caller can enforce v1.1 response limits
            (:func:`panelapp_link.mcp.untrusted_content.enforce_untrusted_text_limits`)
            once over the whole response.

    Returns:
        A JSON-ready dict trimmed to the mode. ``description`` and each
        ``types[].description`` (standard/full only) are typed ``untrusted_text``
        objects, never bare strings.
    """
    region = row.get("region")
    panel_id = row.get("panel_id")
    if mode == "full":
        full_out = dict(row)
        full_out["n_genes"] = full_out.pop("number_of_genes", 0)
        full_out["n_regions"] = full_out.pop("number_of_regions", 0)
        full_out["n_strs"] = full_out.pop("number_of_strs", 0)
        _fence_panel_description(full_out, region, panel_id, fenced)
        if "types" in full_out:
            full_out["types"] = _fence_panel_types(
                full_out["types"], region=region, panel_id=panel_id, fenced=fenced
            )
        return full_out

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
            "types": _fence_panel_types(
                row.get("types", []), region=region, panel_id=panel_id, fenced=fenced
            ),
            "entity_counts": row.get("entity_counts", {}),
            "confidence_counts": row.get("confidence_counts", {}),
        }
    )
    _fence_panel_description(out, region, panel_id, fenced)
    return out


def _entity_record_id(row: dict[str, Any]) -> str:
    """``panel:{region}:{id}#gene:{symbol}`` for genes; falls back to
    ``#entity:{name}`` for region/str entities, which carry no ``gene_symbol``.
    The region is part of the record id because PanelApp panel ids are per-region.
    """
    base = _panel_record_id(row.get("region"), row.get("panel_id"))
    gene_symbol = row.get("gene_symbol")
    if gene_symbol:
        return f"{base}#gene:{gene_symbol}"
    return f"{base}#entity:{row.get('entity_name')}"


def _fence_prose_list(
    values: list[Any], *, record_id: str, fenced: list[UntrustedText] | None
) -> list[dict[str, Any]]:
    """Fence every string element of a curator prose list (phenotypes/evidence)
    as its own ``untrusted_text`` object -- each list element is independently
    sourced upstream prose, not one combined field.
    """
    out: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        obj = fence_untrusted_text(value, source="panelapp", record_id=record_id)
        if fenced is not None:
            fenced.append(obj)
        out.append(obj.model_dump(mode="json"))
    return out


def shape_entity(
    row: dict[str, Any],
    mode: ResponseMode,
    fenced: list[UntrustedText] | None = None,
) -> dict[str, Any]:
    """Shape a normalized panel entity row (gene/region/str) for a response mode.

    Args:
        row: A normalized entity dict (decoded list columns and ``extra`` object).
        mode: One of ``minimal``/``compact``/``standard``/``full``.
        fenced: Optional accumulator collecting every ``UntrustedText`` object
            fenced by this call, so the caller can enforce v1.1 response limits
            once over the whole (possibly many-entity) response.

    Returns:
        A JSON-ready dict trimmed to the mode. ``phenotypes`` (standard/full)
        and ``evidence`` (full only) are lists of typed ``untrusted_text``
        objects, one per curator-prose string, never bare strings.
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

    record_id = _entity_record_id(row)
    out.update(
        {
            "penetrance": row.get("penetrance"),
            "phenotypes": _fence_prose_list(
                row.get("phenotypes", []), record_id=record_id, fenced=fenced
            ),
            "extra": row.get("extra", {}),
        }
    )
    if mode == "standard":
        return out

    # full
    out.update(
        {
            "evidence": _fence_prose_list(
                row.get("evidence", []), record_id=record_id, fenced=fenced
            ),
            "publications": row.get("publications", []),
            "omim": row.get("omim", []),
            "tags": row.get("tags", []),
        }
    )
    return out


def shape_gene_panel_hit(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one panel a gene appears on (a gene->panel hit row).

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
    """Shape a rolled-up gene summary row for output.

    Drops internal columns (``gene_symbol_upper``, ``max_confidence_rank``).
    """
    return {
        "gene_symbol": row.get("gene_symbol"),
        "hgnc_id": row.get("hgnc_id"),
        "panel_count": row.get("panel_count", 0),
        "regions": row.get("regions", []),
        "max_confidence_label": row.get("max_confidence_label"),
    }
