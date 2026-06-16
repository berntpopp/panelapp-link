"""Tests for the read-only PanelApp repository over the built fixtures DB."""

from __future__ import annotations

from pathlib import Path

import pytest

from panelapp_link.constants import CONFIDENCE_RANK
from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.exceptions import DataUnavailableError


def test_get_meta_decodes_versions(repository: PanelAppRepository) -> None:
    """get_meta returns provenance with a decoded panel_versions mapping."""
    meta = repository.get_meta()
    assert meta["schema_version"] == "1"
    assert meta["uk_panel_count"] == 4
    assert meta["panel_versions"]["uk"]["1207"] == "2.1"


def test_search_panels_hit(repository: PanelAppRepository) -> None:
    """A name search returns the matching panel summary."""
    hits = repository.search_panels(
        "intellectual disability", regions=["uk", "australia"], limit=10
    )
    names = {h["name"] for h in hits}
    assert "Intellectual disability" in names
    hit = next(h for h in hits if h["name"] == "Intellectual disability")
    assert hit["region"] == "uk"
    assert isinstance(hit["relevant_disorders"], list)


def test_search_panels_empty_query_lists_all(repository: PanelAppRepository) -> None:
    """An empty query lists panels by name (region-filtered)."""
    hits = repository.search_panels("", regions=["australia"], limit=50)
    assert hits
    assert all(h["region"] == "australia" for h in hits)


def test_search_panels_region_filter(repository: PanelAppRepository) -> None:
    """The region filter restricts results to the requested regions."""
    hits = repository.search_panels("achromatopsia", regions=["australia"], limit=10)
    assert any(h["name"] == "Achromatopsia" for h in hits)


def test_get_panel_returns_detail_with_counts(repository: PanelAppRepository) -> None:
    """get_panel returns a panel with decoded JSON and entity counts."""
    panel = repository.get_panel("uk", 285)
    assert panel is not None
    assert panel["name"] == "Intellectual disability"
    assert isinstance(panel["types"], list)
    assert panel["entity_counts"]["gene"] >= 1
    assert panel["entity_counts"]["region"] >= 1
    assert panel["entity_counts"]["str"] >= 1


def test_get_panel_not_found(repository: PanelAppRepository) -> None:
    """A missing panel returns None."""
    assert repository.get_panel("uk", 999999) is None


def test_get_panel_entities_gene_filter(repository: PanelAppRepository) -> None:
    """Filtering by entity_type='gene' returns only gene entities."""
    genes = repository.get_panel_entities("uk", 285, "gene", limit=100)
    assert genes
    assert all(e["entity_type"] == "gene" for e in genes)
    assert any(e["entity_name"] == "AAAS" for e in genes)


def test_get_panel_entities_region_and_str_filter(repository: PanelAppRepository) -> None:
    """Type filters isolate region and str entities respectively."""
    regions = repository.get_panel_entities("uk", 285, "region", limit=100)
    strs = repository.get_panel_entities("uk", 285, "str", limit=100)
    assert all(e["entity_type"] == "region" for e in regions)
    assert all(e["entity_type"] == "str" for e in strs)
    # Region extras are decoded from extra_json.
    loss = next(e for e in regions if e["entity_name"] == "ISCA-37390-Loss")
    assert loss["extra"]["type_of_variants"] == "cnv_loss"


def test_get_panel_entities_all_no_type_filter(repository: PanelAppRepository) -> None:
    """entity_type='all' returns every entity type."""
    everything = repository.get_panel_entities("uk", 285, "all", limit=1000)
    kinds = {e["entity_type"] for e in everything}
    assert {"gene", "region", "str"} <= kinds


def test_get_panel_entities_min_rank(repository: PanelAppRepository) -> None:
    """min_rank filters out entities below the requested confidence rank."""
    green_only = repository.get_panel_entities(
        "uk", 285, "all", min_rank=CONFIDENCE_RANK["green"], limit=1000
    )
    assert green_only
    assert all(e["confidence_rank"] >= CONFIDENCE_RANK["green"] for e in green_only)
    # The amber STR ATXN10_ATTCT must be excluded.
    assert not any(e["entity_name"] == "ATXN10_ATTCT" for e in green_only)


def test_get_gene_panels_by_symbol(repository: PanelAppRepository) -> None:
    """get_gene_panels by symbol returns the panels a gene appears on."""
    hits = repository.get_gene_panels(gene_symbol_upper="HMBS", regions=["uk", "australia"])
    assert hits
    assert all(h["gene_symbol"] == "HMBS" for h in hits)
    assert any(h["panel_id"] == 1207 for h in hits)


def test_get_gene_panels_by_hgnc(repository: PanelAppRepository) -> None:
    """get_gene_panels by HGNC id resolves the same gene."""
    hits = repository.get_gene_panels(hgnc_id="HGNC:4982", regions=["uk", "australia"])
    assert any(h["panel_id"] == 1207 for h in hits)


def test_resolve_gene_by_symbol_and_hgnc(repository: PanelAppRepository) -> None:
    """resolve_gene returns rolled-up rows by symbol and by HGNC id."""
    by_symbol = repository.resolve_gene(gene_symbol_upper="ATF6")
    assert len(by_symbol) == 1
    assert by_symbol[0]["gene_symbol"] == "ATF6"
    assert by_symbol[0]["regions"] == ["australia"]
    by_hgnc = repository.resolve_gene(hgnc_id="HGNC:791")
    assert by_hgnc and by_hgnc[0]["gene_symbol"] == "ATF6"


def test_get_gene_not_found(repository: PanelAppRepository) -> None:
    """get_gene returns None for an unknown symbol."""
    assert repository.get_gene("NOSUCHGENE") is None


def test_missing_database_raises(tmp_path: Path) -> None:
    """Opening a non-existent database raises DataUnavailableError."""
    with pytest.raises(DataUnavailableError):
        PanelAppRepository(tmp_path / "absent.sqlite")
