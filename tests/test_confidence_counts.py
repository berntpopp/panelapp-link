"""WS-6a: additive confidence_counts; entity_counts stays integer."""

from __future__ import annotations

from panelapp_link.services import shaping

_DETAIL = {
    "id": 283,
    "name": "Cystic kidney disease",
    "stats": {"number_of_genes": 3, "number_of_regions": 0, "number_of_strs": 0},
    "genes": [
        {
            "entity_type": "gene",
            "entity_name": "PKD1",
            "confidence_level": "3",
            "gene_data": {"gene_symbol": "PKD1"},
        },
        {
            "entity_type": "gene",
            "entity_name": "PKD2",
            "confidence_level": "3",
            "gene_data": {"gene_symbol": "PKD2"},
        },
        {
            "entity_type": "gene",
            "entity_name": "GANAB",
            "confidence_level": "2",
            "gene_data": {"gene_symbol": "GANAB"},
        },
    ],
}


def test_normalize_adds_confidence_counts() -> None:
    row = shaping.normalize_panel(_DETAIL, "uk")
    assert row["entity_counts"] == {"gene": 3, "region": 0, "str": 0}  # unchanged, integers
    assert row["confidence_counts"]["gene"] == {"green": 2, "amber": 1, "red": 0}


def test_standard_exposes_confidence_counts_compact_does_not() -> None:
    row = shaping.normalize_panel(_DETAIL, "uk")
    assert "confidence_counts" in shaping.shape_panel(row, "standard")
    assert "confidence_counts" in shaping.shape_panel(row, "full")
    assert "confidence_counts" not in shaping.shape_panel(row, "compact")
    assert "confidence_counts" not in shaping.shape_panel(row, "minimal")
