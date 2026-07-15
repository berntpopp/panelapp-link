"""Pure helpers for the live PanelApp service.

Stateless functions that filter/select/reduce raw PanelApp payloads. Kept out of
``panelapp_service`` to keep that module within its line budget and to make these
small transforms independently testable.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from panelapp_link.constants import CONFIDENCE_RANK, confidence_label
from panelapp_link.exceptions import InvalidInputError

# An HGNC CURIE (``HGNC:1100``) in the gene-lookup position. PanelApp is keyed by
# gene SYMBOL only (``/genes/?entity_name=``); there is no server-side HGNC lookup
# (an unknown ``?hgnc_id=`` filter is ignored and returns the whole gene list). A
# CURIE therefore either misses (UK exact match) or LOOSELY matches one region (AU),
# so as the lookup key it half-answers with a silently dropped region (issue #25 D3).
_HGNC_CURIE_RE = re.compile(r"^HGNC:\d+$", re.IGNORECASE)


def is_hgnc_curie(value: str) -> bool:
    """True if ``value`` is structurally an HGNC CURIE (e.g. HGNC:1100)."""
    return bool(_HGNC_CURIE_RE.match(value))


def reject_hgnc_curie(symbol: str) -> None:
    """Reject an HGNC CURIE used as the gene-lookup key (issue #25 D3).

    PanelApp resolves genes by SYMBOL; a CURIE in the gene_symbol/query position is
    not a lookup key here. ``get_gene_panels``'s ``hgnc_id`` is a separate optional
    result FILTER and is unaffected.
    """
    if is_hgnc_curie(symbol):
        raise InvalidInputError(
            "An HGNC id is not a gene-lookup key here; PanelApp is queried by "
            "approved gene symbol. Pass gene_symbol (e.g. SCN1A).",
            field="gene_symbol",
        )


def validate_panel_id(panel_id: int) -> int:
    """Reject a non-positive panel id before it is interpolated into a URL (#25 D4).

    ``/panels/-1/`` is not a valid resource; without this guard a negative id leaked
    an unrelated real panel (``get_panel(-1)`` returned the COVID-19 panel). Mirrors
    the ``limit >= 1`` / ``offset >= 0`` boundary guards.
    """
    if panel_id < 1:
        raise InvalidInputError("panel_id must be >= 1.", field="panel_id")
    return panel_id


def min_rank(min_confidence: str | None) -> int | None:
    """Map a min_confidence label to a numeric rank floor, validating it."""
    if min_confidence is None:
        return None
    rank = CONFIDENCE_RANK.get(min_confidence)
    if rank is None:
        raise InvalidInputError(
            f"Invalid min_confidence. Use one of: {', '.join(CONFIDENCE_RANK)}.",
            field="min_confidence",
        )
    return rank


def encode_cursor(offset: int) -> str:
    """Encode an opaque, url-safe ``{"offset": N}`` cursor (no padding)."""
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Decode a cursor token to its offset; raise ``InvalidInputError`` on garbage."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        offset = int(payload["offset"])
    except Exception as exc:  # malformed base64 / json / missing key
        raise InvalidInputError("cursor is malformed.", field="cursor") from exc
    if offset < 0:
        raise InvalidInputError("cursor offset is invalid.", field="cursor")
    return offset


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


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: Any) -> list[str]:
    """Lowercase alphanumeric word tokens of a value (``[]`` for blanks/None)."""
    return _TOKEN_RE.findall(str(text or "").lower())


def _weighted_fields(panel: dict[str, Any]) -> list[tuple[int, str]]:
    """(weight, text) searchable fields; higher weight = more relevant field."""
    fields: list[tuple[int, str]] = [(3, str(panel.get("name") or ""))]
    fields += [(2, str(d)) for d in (panel.get("relevant_disorders") or [])]
    fields.append((1, str(panel.get("disease_group") or "")))
    fields.append((1, str(panel.get("disease_sub_group") or "")))
    return fields


def panel_match_score(panel: dict[str, Any], needle: str) -> int:
    """Relevance score for ``needle`` vs a panel (0 = no match).

    Every query token must word-prefix-match a *whole word* within a single
    searchable field (so ``renal`` does not match ``adrenal``). The score is the
    best matching field's weight: name (3) > relevant_disorders (2) > disease
    group/sub-group (1).
    """
    q_tokens = _tokens(needle)
    if not q_tokens:
        return 0
    best = 0
    for weight, text in _weighted_fields(panel):
        words = _tokens(text)
        if words and all(any(w.startswith(qt) for w in words) for qt in q_tokens):
            best = max(best, weight)
    return best


def panel_matches(panel: dict[str, Any], needle: str) -> bool:
    """True when ``needle`` matches a panel by word-prefix (see panel_match_score)."""
    return panel_match_score(panel, needle) > 0


def rank_panels(rows: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    """Sort normalized panel rows: relevance desc, then name, then region.

    An empty ``needle`` preserves the prior alphabetical (name, region) order.
    """
    if not (needle or "").strip():
        return sorted(rows, key=lambda p: ((p.get("name") or "").lower(), p.get("region") or ""))
    return sorted(
        rows,
        key=lambda p: (
            -panel_match_score(p, needle),
            (p.get("name") or "").lower(),
            p.get("region") or "",
        ),
    )


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
    """Roll a gene identity (symbol, hgnc id, panel count, regions, max label) up.

    ``symbol``/``hgnc_id`` are gene METADATA and come from ``results`` (every raw
    hit carries the same identity, filtered out or not). But ``panel_count``,
    ``regions``, and ``max_confidence_label`` are properties of the RETURNED set,
    so they are derived from ``hits`` -- the post-filter rows the caller already
    shaped into ``panels``. Keying them off ``results`` was issue #25 D1: a
    ``min_confidence=green`` call reported ``panel_count`` from the unfiltered
    ``len(results)`` (e.g. 13) beside a 10-element green ``panels`` array, so the
    count contradicted the array and matched the unfiltered call.
    """
    gene_symbol = symbol.upper()
    hgnc_id: str | None = None
    for _region_key, result in results:
        gene_data = result.get("gene_data") or {}
        if gene_data.get("gene_symbol"):
            gene_symbol = gene_data["gene_symbol"]
        if hgnc_id is None and gene_data.get("hgnc_id"):
            hgnc_id = gene_data["hgnc_id"]

    regions = sorted({str(hit.get("region")) for hit in hits if hit.get("region")})
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
        "panel_count": len(hits),
        "regions": regions,
        "max_confidence_label": max_label,
    }
