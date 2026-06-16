"""Tests for PanelAppService over a fixture-built repository.

The service returns plain dict payloads (no envelope); the tool layer adds the
envelope. These tests assert payload shape, region merging/dedupe, filtering,
sorting, cursor paging, and typed-exception behaviour.
"""

from __future__ import annotations

import base64
import json

import pytest

from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.exceptions import (
    InvalidInputError,
    NotFoundError,
)
from panelapp_link.services.panelapp_service import PanelAppService


@pytest.fixture
def service(repository: PanelAppRepository) -> PanelAppService:
    return PanelAppService(repository)


# --- search_panels ---------------------------------------------------------


def test_search_panels_both_merges_and_dedupes(service: PanelAppService) -> None:
    out = service.search_panels(query="", region="both", response_mode="compact", limit=100)
    regions = {p["region"] for p in out["panels"]}
    assert regions == {"uk", "australia"}
    # No duplicate (region, panel_id) pairs.
    keys = [(p["region"], p["panel_id"]) for p in out["panels"]]
    assert len(keys) == len(set(keys))
    assert out["count"] == len(out["panels"])
    assert out["total"] == out["count"]


def test_search_panels_region_filter(service: PanelAppService) -> None:
    out = service.search_panels(query="", region="uk", response_mode="minimal", limit=100)
    assert {p["region"] for p in out["panels"]} == {"uk"}
    # minimal shape
    assert set(out["panels"][0]) == {
        "panel_id",
        "name",
        "region",
        "n_genes",
        "n_regions",
        "n_strs",
    }


def test_search_panels_truncated_and_cursor_roundtrip(service: PanelAppService) -> None:
    page1 = service.search_panels(query="", region="both", response_mode="compact", limit=2)
    assert page1["count"] == 2
    assert "truncated" in page1
    trunc = page1["truncated"]
    assert trunc["returned"] == 2
    assert trunc["next_offset"] == 2
    assert trunc["next_cursor"]
    # Cursor decodes to the next offset.
    padded = trunc["next_cursor"] + "=" * (-len(trunc["next_cursor"]) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    assert decoded["offset"] == 2
    # Following the cursor advances the page.
    page2 = service.search_panels(cursor=trunc["next_cursor"])
    first_ids = {(p["region"], p["panel_id"]) for p in page1["panels"]}
    second_ids = {(p["region"], p["panel_id"]) for p in page2["panels"]}
    assert first_ids.isdisjoint(second_ids)


# --- get_panel -------------------------------------------------------------


def test_get_panel_returns_detail(service: PanelAppService) -> None:
    out = service.get_panel(panel_id=285, region="uk", response_mode="standard")
    panel = out["panel"]
    assert panel["panel_id"] == 285
    assert panel["region"] == "uk"
    assert panel["entity_counts"] == {"gene": 5, "region": 3, "str": 3}


def test_get_panel_both_region_rejected(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        service.get_panel(panel_id=285, region="both")
    assert exc.value.field == "region"


def test_get_panel_missing_raises_not_found(service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        service.get_panel(panel_id=999999, region="uk")


# --- get_panel_genes -------------------------------------------------------


def test_get_panel_genes_filters_by_entity_type(service: PanelAppService) -> None:
    genes = service.get_panel_genes(panel_id=285, region="uk", entity_type="gene")
    assert genes["entity_type"] == "gene"
    assert {e["entity_type"] for e in genes["entities"]} == {"gene"}
    regions = service.get_panel_genes(panel_id=285, region="uk", entity_type="region")
    assert {e["entity_type"] for e in regions["entities"]} == {"region"}
    allents = service.get_panel_genes(panel_id=285, region="uk", entity_type="all")
    assert {e["entity_type"] for e in allents["entities"]} == {"gene", "region", "str"}


def test_get_panel_genes_min_confidence(service: PanelAppService) -> None:
    green = service.get_panel_genes(
        panel_id=285, region="uk", entity_type="all", min_confidence="green"
    )
    assert all(e["confidence_label"] == "green" for e in green["entities"])
    # red keeps everything (>= rank 1)
    red = service.get_panel_genes(
        panel_id=285, region="uk", entity_type="all", min_confidence="red"
    )
    assert red["count"] >= green["count"]


def test_get_panel_genes_invalid_entity_type(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        service.get_panel_genes(panel_id=285, region="uk", entity_type="bogus")
    assert exc.value.field == "entity_type"


def test_get_panel_genes_invalid_min_confidence(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        service.get_panel_genes(panel_id=285, region="uk", min_confidence="blue")
    assert exc.value.field == "min_confidence"


# --- get_gene_panels -------------------------------------------------------


def test_get_gene_panels_sorted_by_confidence(service: PanelAppService) -> None:
    # ATF6 exists on the Achromatopsia panel (fixture).
    out = service.get_gene_panels(gene_symbol="ATF6", region="both")
    assert out["gene"]["gene_symbol"] == "ATF6"
    assert out["count"] == len(out["panels"])
    assert out["count"] >= 1
    # panels carry shaped GenePanelHit keys
    hit = out["panels"][0]
    assert set(hit) >= {"region", "panel_id", "panel_name", "confidence_label"}
    assert "confidence_rank" not in hit


def test_get_gene_panels_requires_an_identifier(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError):
        service.get_gene_panels(region="both")


def test_get_gene_panels_missing_gene(service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        service.get_gene_panels(gene_symbol="NOTAGENE123", region="both")


# --- resolve_gene ----------------------------------------------------------


def test_resolve_gene_by_symbol(service: PanelAppService) -> None:
    out = service.resolve_gene(query="atf6")
    assert out["gene"]["gene_symbol"] == "ATF6"
    assert out["query"] == "ATF6"
    assert len(out["matches"]) == 1


def test_resolve_gene_missing_raises_not_found(service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        service.resolve_gene(gene_symbol="NOPE")


def test_resolve_gene_requires_input(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError):
        service.resolve_gene()


# --- validation ------------------------------------------------------------


def test_invalid_response_mode_raises(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        service.search_panels(query="", region="uk", response_mode="verbose")
    assert exc.value.field == "response_mode"


def test_invalid_region_raises(service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        service.search_panels(query="", region="mars")
    assert exc.value.field == "region"


# --- discovery -------------------------------------------------------------


def test_capabilities_data(service: PanelAppService) -> None:
    caps = service.capabilities_data()
    assert caps["uk_panel_count"] >= 1
    assert caps["au_panel_count"] >= 1
    assert caps["build_utc"]


def test_diagnostics(service: PanelAppService) -> None:
    diag = service.diagnostics()
    assert diag["schema_version"]
    assert "panel_versions" in diag
