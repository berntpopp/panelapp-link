"""Tests for the shared MCP-tool argument helpers (panelapp_link.mcp.tools._args).

These pure functions give early, friendly ``invalid_input`` errors at the tool
boundary: region fan-out, the mutually-exclusive gene aliases, and the
confidence/entity vocab. They are exercised directly here.
"""

from __future__ import annotations

import pytest

from panelapp_link.constants import CONFIDENCE_RANK
from panelapp_link.exceptions import InvalidInputError
from panelapp_link.mcp.tools._args import (
    coalesce_gene,
    normalize_region,
    validate_entity_type,
    validate_min_confidence,
)
from panelapp_link.models.enums import ENTITY_TYPES

# --- normalize_region ------------------------------------------------------


def test_normalize_region_both_fans_out() -> None:
    assert normalize_region("both") == ["uk", "australia"]


@pytest.mark.parametrize(("region", "expected"), [("uk", ["uk"]), ("australia", ["australia"])])
def test_normalize_region_single(region: str, expected: list[str]) -> None:
    assert normalize_region(region) == expected


def test_normalize_region_returns_a_fresh_list() -> None:
    # The helper must not hand back the internal map's list (mutation safety).
    first = normalize_region("both")
    first.append("mars")
    assert normalize_region("both") == ["uk", "australia"]


def test_normalize_region_invalid_raises() -> None:
    with pytest.raises(InvalidInputError) as exc:
        normalize_region("mars")
    assert exc.value.field == "region"


# --- coalesce_gene ---------------------------------------------------------


def test_coalesce_gene_precedence_hgnc_over_symbol_over_query() -> None:
    assert coalesce_gene("BRCA1", "HGNC:1100", "free", required=True) == "HGNC:1100"
    assert coalesce_gene("BRCA1", None, "free", required=True) == "BRCA1"
    assert coalesce_gene(None, None, "free", required=True) == "free"


def test_coalesce_gene_strips_whitespace_and_treats_blank_as_absent() -> None:
    assert coalesce_gene("  BRCA1  ", None, None, required=True) == "BRCA1"
    # Blank-only symbol falls through to the query.
    assert coalesce_gene("   ", None, "BRCA2", required=True) == "BRCA2"


def test_coalesce_gene_optional_returns_none_when_nothing_supplied() -> None:
    assert coalesce_gene(None, None, None, required=False) is None
    assert coalesce_gene("", "  ", "", required=False) is None


def test_coalesce_gene_required_raises_when_nothing_supplied() -> None:
    with pytest.raises(InvalidInputError) as exc:
        coalesce_gene(None, None, None, required=True)
    assert exc.value.field == "gene_symbol"


# --- validate_min_confidence ----------------------------------------------


def test_validate_min_confidence_none_passes_through() -> None:
    assert validate_min_confidence(None) is None


@pytest.mark.parametrize("label", list(CONFIDENCE_RANK))
def test_validate_min_confidence_valid_labels(label: str) -> None:
    assert validate_min_confidence(label) == label


def test_validate_min_confidence_invalid_raises() -> None:
    with pytest.raises(InvalidInputError) as exc:
        validate_min_confidence("blue")
    assert exc.value.field == "min_confidence"


# --- validate_entity_type --------------------------------------------------


@pytest.mark.parametrize("entity_type", list(ENTITY_TYPES))
def test_validate_entity_type_valid(entity_type: str) -> None:
    assert validate_entity_type(entity_type) == entity_type


def test_validate_entity_type_invalid_raises() -> None:
    with pytest.raises(InvalidInputError) as exc:
        validate_entity_type("bogus")
    assert exc.value.field == "entity_type"
