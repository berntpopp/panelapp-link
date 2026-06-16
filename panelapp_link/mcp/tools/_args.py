"""Shared MCP-tool argument helpers.

These give early, friendly ``invalid_input`` errors at the tool boundary and
centralize the few argument shapes shared across tools (region fan-out, the
mutually-exclusive gene aliases, the confidence/entity vocab). The service layer
re-validates everything authoritatively; these helpers are intentionally thin
and exist where they make a tool body read clearly or catch a bad call early.
"""

from __future__ import annotations

from typing import Literal, overload

from panelapp_link.constants import CONFIDENCE_RANK
from panelapp_link.exceptions import InvalidInputError
from panelapp_link.models.enums import ENTITY_TYPES

# region argument -> repository region keys (mirrors the service mapping).
_REGION_MAP: dict[str, list[str]] = {
    "both": ["uk", "australia"],
    "uk": ["uk"],
    "australia": ["australia"],
}


def normalize_region(region: str) -> list[str]:
    """Map a ``region`` argument to repository region keys.

    ``"both"`` -> ``["uk", "australia"]``; ``"uk"``/``"australia"`` -> a single
    element list. Raises ``InvalidInputError`` (field ``region``) otherwise.
    """
    keys = _REGION_MAP.get(region)
    if keys is None:
        raise InvalidInputError(
            f"Invalid region {region!r}. Use 'uk', 'australia', or 'both'.",
            field="region",
        )
    return list(keys)


@overload
def coalesce_gene(
    gene_symbol: str | None,
    hgnc_id: str | None,
    query: str | None = None,
    *,
    required: Literal[True],
) -> str: ...


@overload
def coalesce_gene(
    gene_symbol: str | None,
    hgnc_id: str | None,
    query: str | None = None,
    *,
    required: Literal[False],
) -> str | None: ...


def coalesce_gene(
    gene_symbol: str | None,
    hgnc_id: str | None,
    query: str | None = None,
    *,
    required: bool,
) -> str | None:
    """Collapse the canonical gene aliases into one usable identifier.

    PanelApp resolves a gene from an approved ``gene_symbol``, an ``hgnc_id``
    CURIE, or free-text ``query``; these are mutually exclusive aliases for the
    single polymorphic service ``gene`` input. Returns the first supplied value
    (``hgnc_id`` > ``gene_symbol`` > ``query``) or ``None`` when none is given and
    the caller permits an absent gene. With ``required=True`` the return is
    narrowed to ``str`` (a missing value raises before returning).

    Raises ``InvalidInputError`` (-> ``invalid_input`` envelope) when nothing
    usable is supplied and ``required`` is True. Call this inside the tool's
    ``run_mcp_tool`` body so the raise is enveloped.
    """
    hid = (hgnc_id or "").strip() or None
    sym = (gene_symbol or "").strip() or None
    q = (query or "").strip() or None
    value = hid or sym or q
    if value is None and required:
        raise InvalidInputError(
            "Provide `gene_symbol` (approved symbol), `hgnc_id` (HGNC CURIE), or a `query`.",
            field="gene_symbol",
        )
    return value


def validate_min_confidence(value: str | None) -> str | None:
    """Validate a ``min_confidence`` traffic-light label (or ``None``).

    Returns the label unchanged when valid; raises ``InvalidInputError`` (field
    ``min_confidence``) otherwise.
    """
    if value is None:
        return None
    if value not in CONFIDENCE_RANK:
        raise InvalidInputError(
            f"Invalid min_confidence {value!r}. Use one of: {', '.join(CONFIDENCE_RANK)}.",
            field="min_confidence",
        )
    return value


def validate_entity_type(value: str) -> str:
    """Validate an ``entity_type`` against the vocabulary.

    Returns the value unchanged when valid; raises ``InvalidInputError`` (field
    ``entity_type``) otherwise.
    """
    if value not in ENTITY_TYPES:
        raise InvalidInputError(
            f"Invalid entity_type {value!r}. Use one of: {', '.join(ENTITY_TYPES)}.",
            field="entity_type",
        )
    return value
