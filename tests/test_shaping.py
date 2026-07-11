"""Tests for response_mode shaping (pure functions over repo dict rows)."""

from __future__ import annotations

from typing import Any

import pytest

from panelapp_link.services import shaping


def _panel_row() -> dict[str, Any]:
    return {
        "region": "uk",
        "panel_id": 1207,
        "hash_id": "abc123",
        "name": "Acute intermittent porphyria",
        "name_upper": "ACUTE INTERMITTENT PORPHYRIA",
        "version": "2.5",
        "version_created": "2024-01-02T00:00:00",
        "disease_group": "Metabolic disorders",
        "disease_sub_group": "Porphyria",
        "status": "public",
        "description": "An AU-only description",
        "relevant_disorders": ["AIP", "Porphyria"],
        "types": [{"name": "GMS signed-off"}],
        "number_of_genes": 5,
        "number_of_regions": 0,
        "number_of_strs": 0,
        "signed_off_version": "2.0",
        "signed_off_date": "2023-06-01",
        "entity_counts": {"gene": 5},
    }


def _entity_row() -> dict[str, Any]:
    return {
        "region": "uk",
        "panel_id": 285,
        "entity_type": "gene",
        "entity_name": "ATF6",
        "gene_symbol": "ATF6",
        "gene_symbol_upper": "ATF6",
        "hgnc_id": "HGNC:791",
        "confidence_level": "3",
        "confidence_label": "green",
        "confidence_rank": 3,
        "mode_of_inheritance": "BIALLELIC",
        "penetrance": "Complete",
        "phenotypes": ["Achromatopsia"],
        "evidence": ["Expert Review Green"],
        "publications": ["12345678"],
        "omim": ["616517"],
        "tags": ["tag1"],
        "extra": {"chromosome": "1", "grch38_coordinates": [1, 2]},
    }


def _gene_panel_hit_row() -> dict[str, Any]:
    return {
        "region": "uk",
        "panel_id": 285,
        "panel_name": "Achromatopsia",
        "gene_symbol": "ATF6",
        "hgnc_id": "HGNC:791",
        "confidence_level": "3",
        "confidence_label": "green",
        "confidence_rank": 3,
        "mode_of_inheritance": "BIALLELIC",
    }


def _gene_row() -> dict[str, Any]:
    return {
        "gene_symbol": "ATF6",
        "gene_symbol_upper": "ATF6",
        "hgnc_id": "HGNC:791",
        "panel_count": 3,
        "regions": ["australia", "uk"],
        "max_confidence_label": "green",
        "max_confidence_rank": 3,
    }


# --- shape_panel -----------------------------------------------------------


def test_shape_panel_minimal_has_ids_name_counts_only() -> None:
    out = shaping.shape_panel(_panel_row(), "minimal")
    assert set(out) == {"panel_id", "name", "region", "n_genes", "n_regions", "n_strs"}
    assert out["panel_id"] == 1207
    assert out["n_genes"] == 5
    assert "version" not in out
    assert "description" not in out


def test_shape_panel_compact_adds_summary_fields() -> None:
    out = shaping.shape_panel(_panel_row(), "compact")
    for key in (
        "version",
        "disease_group",
        "disease_sub_group",
        "status",
        "signed_off_version",
        "signed_off_date",
        "relevant_disorders",
    ):
        assert key in out
    assert out["version"] == "2.5"
    assert out["signed_off_version"] == "2.0"
    # standard-only fields excluded
    assert "version_created" not in out
    assert "description" not in out
    assert "entity_counts" not in out


def test_shape_panel_standard_adds_detail() -> None:
    out = shaping.shape_panel(_panel_row(), "standard")
    for key in ("version_created", "description", "types", "entity_counts"):
        assert key in out
    assert out["entity_counts"] == {"gene": 5}
    # description is fenced as a v1.1 untrusted_text object, not a bare string
    # (Response-Envelope Standard v1.1; panelapp_link/services/shaping.py).
    assert out["description"]["kind"] == "untrusted_text"
    assert out["description"]["text"] == "An AU-only description"
    # full-only raw key excluded
    assert "name_upper" not in out


def test_shape_panel_full_returns_full_row() -> None:
    row = _panel_row()
    out = shaping.shape_panel(row, "full")
    # full keeps every untrimmed key (e.g. raw name_upper / hash_id) ...
    assert "name_upper" in out
    # ... but the shared count fields are normalized to n_* in every mode (M1):
    # number_of_* never leaks out of the shaper.
    expected = dict(row)
    expected["n_genes"] = expected.pop("number_of_genes")
    expected["n_regions"] = expected.pop("number_of_regions")
    expected["n_strs"] = expected.pop("number_of_strs")
    # description is fenced (v1.1 untrusted_text), not the bare raw string --
    # compare everything else verbatim, then check the fenced field separately.
    expected_description = expected.pop("description")
    out_description = out.pop("description")
    assert out == expected
    assert not any(k.startswith("number_of_") for k in out)
    assert out_description["kind"] == "untrusted_text"
    assert out_description["text"] == expected_description


# --- shape_entity ----------------------------------------------------------


def test_shape_entity_minimal_keys() -> None:
    out = shaping.shape_entity(_entity_row(), "minimal")
    assert set(out) == {"entity_name", "entity_type", "gene_symbol", "confidence_label"}
    assert out["confidence_label"] == "green"
    assert "hgnc_id" not in out


def test_shape_entity_compact_adds_identity_and_moi() -> None:
    out = shaping.shape_entity(_entity_row(), "compact")
    for key in ("hgnc_id", "confidence_level", "mode_of_inheritance"):
        assert key in out
    assert out["confidence_level"] == "3"
    assert "penetrance" not in out
    assert "phenotypes" not in out


def test_shape_entity_standard_adds_penetrance_phenotypes_extra() -> None:
    out = shaping.shape_entity(_entity_row(), "standard")
    for key in ("penetrance", "phenotypes", "extra"):
        assert key in out
    # phenotypes is a list of fenced v1.1 untrusted_text objects, one per
    # curator-prose string, not bare strings.
    assert len(out["phenotypes"]) == 1
    assert out["phenotypes"][0]["kind"] == "untrusted_text"
    assert out["phenotypes"][0]["text"] == "Achromatopsia"
    # full-only fields excluded
    assert "evidence" not in out
    assert "publications" not in out
    assert "omim" not in out
    assert "tags" not in out


def test_shape_entity_full_adds_evidence_publications_omim_tags() -> None:
    out = shaping.shape_entity(_entity_row(), "full")
    for key in ("evidence", "publications", "omim", "tags", "extra", "phenotypes", "penetrance"):
        assert key in out
    assert out["publications"] == ["12345678"]
    assert out["omim"] == ["616517"]


# --- shape_gene_panel_hit / shape_gene -------------------------------------


def test_shape_gene_panel_hit_keys() -> None:
    out = shaping.shape_gene_panel_hit(_gene_panel_hit_row())
    for key in (
        "region",
        "panel_id",
        "panel_name",
        "confidence_label",
        "confidence_level",
        "mode_of_inheritance",
    ):
        assert key in out
    assert out["confidence_label"] == "green"
    # internal ranking column not surfaced
    assert "confidence_rank" not in out


def test_shape_gene_keys() -> None:
    out = shaping.shape_gene(_gene_row())
    for key in ("gene_symbol", "hgnc_id", "panel_count", "regions", "max_confidence_label"):
        assert key in out
    assert out["regions"] == ["australia", "uk"]
    assert "gene_symbol_upper" not in out
    assert "max_confidence_rank" not in out


@pytest.mark.parametrize("mode", ["minimal", "compact", "standard", "full"])
def test_shape_panel_always_carries_identity(mode: str) -> None:
    out = shaping.shape_panel(_panel_row(), mode)
    assert out["panel_id"] == 1207
    assert out["name"] == "Acute intermittent porphyria"
    assert out["region"] == "uk"


# --- normalize_panel / normalize_entity ------------------------------------


def test_normalize_panel_summary() -> None:
    live = {
        "id": 1207,
        "name": "Acute intermittent porphyria",
        "version": 2.1,
        "disease_group": "Gastrohepatology",
        "relevant_disorders": ["R169"],
        "stats": {"number_of_genes": 1, "number_of_regions": 0, "number_of_strs": 0},
    }
    out = shaping.normalize_panel(live, "uk", {"version": "2.0", "signed_off": "2023-06-01"})
    assert out["panel_id"] == 1207
    assert out["region"] == "uk"
    assert out["version"] == "2.1"  # cast to str
    assert out["number_of_genes"] == 1
    assert out["signed_off_version"] == "2.0"
    assert out["signed_off_date"] == "2023-06-01"
    # No genes/regions/strs -> no entity_counts on a summary.
    assert "entity_counts" not in out


def test_normalize_panel_detail_includes_entity_counts() -> None:
    live = {
        "id": 285,
        "name": "Intellectual disability",
        "stats": {},
        "genes": [{}, {}],
        "regions": [{}],
        "strs": [],
    }
    out = shaping.normalize_panel(live, "uk", None)
    assert out["entity_counts"] == {"gene": 2, "region": 1, "str": 0}
    assert out["signed_off_version"] is None


def test_normalize_entity_gene() -> None:
    live = {
        "entity_type": "gene",
        "entity_name": "HMBS",
        "confidence_level": "3",
        "gene_data": {"gene_symbol": "HMBS", "hgnc_id": "HGNC:4982", "omim_gene": ["609806"]},
        "mode_of_inheritance": "BIALLELIC",
        "phenotypes": ["AIP"],
    }
    out = shaping.normalize_entity(live, "uk", 1207, "Acute intermittent porphyria")
    assert out["gene_symbol"] == "HMBS"
    assert out["gene_symbol_upper"] == "HMBS"
    assert out["hgnc_id"] == "HGNC:4982"
    assert out["confidence_label"] == "green"
    assert out["confidence_rank"] == 3
    assert out["omim"] == ["609806"]
    assert out["panel_name"] == "Acute intermittent porphyria"
    assert out["extra"] == {}


def test_normalize_entity_str_packs_extra() -> None:
    live = {
        "entity_type": "str",
        "entity_name": "DMPK_CTG",
        "confidence_level": "3",
        "gene_data": {"gene_symbol": "DMPK"},
        "repeated_sequence": "CTG",
        "chromosome": "19",
        "normal_repeats": 35,
    }
    out = shaping.normalize_entity(live, "uk", 285, "Intellectual disability")
    assert out["extra"]["repeated_sequence"] == "CTG"
    assert out["extra"]["chromosome"] == "19"
    assert out["extra"]["normal_repeats"] == 35
