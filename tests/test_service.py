"""Tests for the live PanelAppService over respx-mocked PanelApp fixtures.

The service returns plain dict payloads (no envelope); the tool layer adds the
envelope. These tests assert payload shape, in-memory search filtering, region
merging/dedupe, entity filtering, gene->panels across regions, resolution, cursor
paging, and typed-exception behaviour -- all without any live network.
"""

from __future__ import annotations

import base64
import json

import pytest

from panelapp_link.exceptions import InvalidInputError, NotFoundError
from panelapp_link.services.panelapp_service import PanelAppService

# --- search_panels ---------------------------------------------------------


async def test_search_panels_both_merges_and_dedupes(live_service: PanelAppService) -> None:
    out = await live_service.search_panels(query="", region="both", limit=100)
    regions = {p["region"] for p in out["panels"]}
    assert regions == {"uk", "australia"}
    keys = [(p["region"], p["panel_id"]) for p in out["panels"]]
    assert len(keys) == len(set(keys))
    assert out["count"] == len(out["panels"])
    assert out["total"] == out["count"]


async def test_search_panels_filters_in_memory(live_service: PanelAppService) -> None:
    out = await live_service.search_panels(query="porphyria", region="uk")
    assert out["total"] == 1
    assert out["panels"][0]["name"] == "Acute intermittent porphyria"


async def test_search_panels_filters_on_disease_group(live_service: PanelAppService) -> None:
    out = await live_service.search_panels(query="neurology", region="uk", response_mode="compact")
    names = {p["name"] for p in out["panels"]}
    assert "Acute rhabdomyolysis" in names


async def test_search_panels_region_filter_minimal_shape(live_service: PanelAppService) -> None:
    out = await live_service.search_panels(
        query="", region="uk", response_mode="minimal", limit=100
    )
    assert {p["region"] for p in out["panels"]} == {"uk"}
    assert set(out["panels"][0]) == {
        "panel_id",
        "name",
        "region",
        "n_genes",
        "n_regions",
        "n_strs",
    }


async def test_search_panels_annotates_signed_off(live_service: PanelAppService) -> None:
    # uk_signedoff fixture has no entry for 1207, so signed_off is None; the
    # annotation path still runs without error and the key is present.
    out = await live_service.search_panels(query="porphyria", region="uk", response_mode="compact")
    panel = out["panels"][0]
    assert "signed_off_version" in panel
    assert "signed_off_date" in panel


async def test_search_panels_truncated_and_cursor_roundtrip(live_service: PanelAppService) -> None:
    page1 = await live_service.search_panels(query="", region="both", limit=2)
    assert page1["count"] == 2
    assert "truncated" in page1
    trunc = page1["truncated"]
    assert trunc["returned"] == 2
    assert trunc["next_offset"] == 2
    padded = trunc["next_cursor"] + "=" * (-len(trunc["next_cursor"]) % 4)
    assert json.loads(base64.urlsafe_b64decode(padded))["offset"] == 2
    page2 = await live_service.search_panels(cursor=trunc["next_cursor"])
    first = {(p["region"], p["panel_id"]) for p in page1["panels"]}
    second = {(p["region"], p["panel_id"]) for p in page2["panels"]}
    assert first.isdisjoint(second)


# --- get_panel -------------------------------------------------------------


async def test_get_panel_returns_detail(live_service: PanelAppService) -> None:
    out = await live_service.get_panel(panel_id=285, region="uk", response_mode="standard")
    panel = out["panel"]
    assert panel["panel_id"] == 285
    assert panel["region"] == "uk"
    assert panel["entity_counts"] == {"gene": 5, "region": 3, "str": 3}


async def test_get_panel_signed_off_annotation(live_service: PanelAppService) -> None:
    # Panel 285 is not in the signed-off fixture; annotation is None but present.
    out = await live_service.get_panel(panel_id=285, region="uk", response_mode="compact")
    assert "signed_off_version" in out["panel"]


async def test_get_panel_both_region_rejected(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel(panel_id=285, region="both")
    assert exc.value.field == "region"


async def test_get_panel_missing_raises_not_found(live_service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        await live_service.get_panel(panel_id=999999, region="uk")


# --- get_panel_genes -------------------------------------------------------


async def test_get_panel_genes_filters_by_entity_type(live_service: PanelAppService) -> None:
    genes = await live_service.get_panel_genes(panel_id=285, region="uk", entity_type="gene")
    assert genes["entity_type"] == "gene"
    assert {e["entity_type"] for e in genes["entities"]} == {"gene"}
    regions = await live_service.get_panel_genes(panel_id=285, region="uk", entity_type="region")
    assert {e["entity_type"] for e in regions["entities"]} == {"region"}
    allents = await live_service.get_panel_genes(panel_id=285, region="uk", entity_type="all")
    assert {e["entity_type"] for e in allents["entities"]} == {"gene", "region", "str"}
    assert allents["total"] == genes["total"] + regions["total"] + 3


async def test_get_panel_genes_min_confidence(live_service: PanelAppService) -> None:
    green = await live_service.get_panel_genes(
        panel_id=285, region="uk", entity_type="all", min_confidence="green"
    )
    assert all(e["confidence_label"] == "green" for e in green["entities"])
    red = await live_service.get_panel_genes(
        panel_id=285, region="uk", entity_type="all", min_confidence="red"
    )
    assert red["count"] >= green["count"]


async def test_get_panel_genes_invalid_entity_type(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel_genes(panel_id=285, region="uk", entity_type="bogus")
    assert exc.value.field == "entity_type"


async def test_get_panel_genes_invalid_min_confidence(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel_genes(panel_id=285, region="uk", min_confidence="blue")
    assert exc.value.field == "min_confidence"


async def test_get_panel_genes_both_region_rejected(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel_genes(panel_id=285, region="both")
    assert exc.value.field == "region"


# --- get_gene_panels -------------------------------------------------------


async def test_get_gene_panels_sorted_by_confidence(live_service: PanelAppService) -> None:
    out = await live_service.get_gene_panels(gene_symbol="PKD1", region="australia")
    assert out["gene"]["gene_symbol"] == "PKD1"
    assert out["count"] == len(out["panels"])
    assert out["count"] >= 1
    hit = out["panels"][0]
    assert set(hit) >= {"region", "panel_id", "panel_name", "confidence_label"}
    assert "confidence_rank" not in hit


async def test_get_gene_panels_across_regions(live_service: PanelAppService) -> None:
    # AAAS resolves on UK only in the fixtures; PKD1 on AU only.
    out = await live_service.get_gene_panels(gene_symbol="AAAS", region="both")
    assert out["gene"]["gene_symbol"] == "AAAS"
    assert {p["region"] for p in out["panels"]} == {"uk"}


async def test_get_gene_panels_hgnc_filter(live_service: PanelAppService) -> None:
    out = await live_service.get_gene_panels(
        gene_symbol="PKD1", hgnc_id="HGNC:9008", region="australia"
    )
    assert out["count"] >= 1
    # A non-matching hgnc filter removes every hit.
    out2 = await live_service.get_gene_panels(
        gene_symbol="PKD1", hgnc_id="HGNC:0000", region="australia"
    )
    assert out2["count"] == 0


async def test_get_gene_panels_min_confidence_filter(live_service: PanelAppService) -> None:
    out = await live_service.get_gene_panels(
        gene_symbol="PKD1", region="australia", min_confidence="green"
    )
    assert all(p["confidence_label"] == "green" for p in out["panels"])


async def test_get_gene_panels_requires_symbol(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_gene_panels(region="both")
    assert exc.value.field == "gene_symbol"


async def test_get_gene_panels_hgnc_only_rejected(live_service: PanelAppService) -> None:
    # PanelApp queries by symbol; an hgnc_id alone cannot drive the query.
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_gene_panels(hgnc_id="HGNC:9008", region="both")
    assert exc.value.field == "gene_symbol"


async def test_get_gene_panels_missing_gene(live_service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        await live_service.get_gene_panels(gene_symbol="NOTAGENE123", region="both")


# --- resolve_gene ----------------------------------------------------------


async def test_resolve_gene_by_symbol(live_service: PanelAppService) -> None:
    out = await live_service.resolve_gene(query="pkd1")
    assert out["gene"]["gene_symbol"] == "PKD1"
    assert out["query"] == "PKD1"
    assert out["gene"]["hgnc_id"] == "HGNC:9008"
    assert len(out["matches"]) == 1


async def test_resolve_gene_max_confidence(live_service: PanelAppService) -> None:
    out = await live_service.resolve_gene(gene_symbol="PKD1")
    assert out["gene"]["max_confidence_label"] == "green"
    assert out["gene"]["panel_count"] >= 1


async def test_resolve_gene_region_scopes_lookup(live_service: PanelAppService) -> None:
    out = await live_service.resolve_gene(gene_symbol="PKD1", region="australia")
    assert out["gene"]["gene_symbol"] == "PKD1"
    assert out["gene"]["regions"] == ["australia"]


async def test_resolve_gene_invalid_region_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError):
        await live_service.resolve_gene(gene_symbol="PKD1", region="mars")


async def test_resolve_gene_missing_raises_not_found(live_service: PanelAppService) -> None:
    with pytest.raises(NotFoundError):
        await live_service.resolve_gene(gene_symbol="NOPE")


async def test_resolve_gene_requires_input(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError):
        await live_service.resolve_gene()


async def test_resolve_gene_blank_query_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError):
        await live_service.resolve_gene(query="   ")


# --- validation ------------------------------------------------------------


async def test_invalid_response_mode_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.search_panels(query="", region="uk", response_mode="verbose")
    assert exc.value.field == "response_mode"


async def test_invalid_region_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.search_panels(query="", region="mars")
    assert exc.value.field == "region"


async def test_clamp_limit_rejects_below_one(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.search_panels(query="", region="uk", limit=0)
    assert exc.value.field == "limit"


async def test_offset_rejects_negative(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.search_panels(query="", region="uk", offset=-1)
    assert exc.value.field == "offset"


def test_limit_clamped_to_max() -> None:
    from panelapp_link.services.panelapp_service import _MAX_LIMIT

    assert PanelAppService._clamp_limit(_MAX_LIMIT + 9999) == _MAX_LIMIT


# --- discovery -------------------------------------------------------------


async def test_capabilities_data(live_service: PanelAppService) -> None:
    caps = live_service.capabilities_data()
    assert caps["mode"] == "live"
    assert "uk" in caps["sources"] and "australia" in caps["sources"]
    assert caps["cache_ttl_seconds"] == 3600


async def test_diagnostics(live_service: PanelAppService) -> None:
    diag = live_service.diagnostics()
    assert diag["mode"] == "live"
    assert diag["sources"]["uk"]
    assert "cache" in diag


# --- caching ----------------------------------------------------------------


async def test_panel_list_is_cached(live_service: PanelAppService) -> None:
    # Two searches hit the same cached panel list; the second still returns data.
    first = await live_service.search_panels(query="", region="uk", limit=100)
    second = await live_service.search_panels(query="", region="uk", limit=100)
    assert first["total"] == second["total"]


# --- cursor decoding edge cases --------------------------------------------


async def test_search_panels_malformed_cursor_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.search_panels(cursor="!!!not-base64!!!")
    assert exc.value.field == "cursor"


async def test_get_panel_genes_malformed_cursor_raises(live_service: PanelAppService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel_genes(panel_id=285, region="uk", cursor="@@bogus@@")
    assert exc.value.field == "cursor"


def test_cursor_negative_offset_rejected() -> None:
    from panelapp_link.services.panelapp_service import _decode_cursor, _encode_cursor

    bad = _encode_cursor(-5)
    with pytest.raises(InvalidInputError) as exc:
        _decode_cursor(bad)
    assert exc.value.field == "cursor"


def test_cursor_roundtrip_unpadded() -> None:
    from panelapp_link.services.panelapp_service import _decode_cursor, _encode_cursor

    for offset in (0, 1, 7, 123, 4096):
        assert _decode_cursor(_encode_cursor(offset)) == offset


# --- B-2: word-boundary search filtering + relevance ranking ---------------


async def test_search_panels_word_prefix_not_substring(live_service: PanelAppService) -> None:
    # "porphyria" is a whole word in "Acute intermittent porphyria".
    hit = await live_service.search_panels(query="porphyria", region="uk")
    assert hit["total"] >= 1
    # "orphyr" is only a mid-word substring -> must NOT match under word-prefix rules.
    miss = await live_service.search_panels(query="orphyr", region="uk")
    assert miss["total"] == 0


async def test_search_panels_ranks_results_by_relevance(live_service: PanelAppService) -> None:
    from panelapp_link.services import _live_helpers as helpers

    out = await live_service.search_panels(query="acute", region="uk", limit=50)
    scores = [helpers.panel_match_score(p, "acute") for p in out["panels"]]
    # rank_panels must return scores in non-increasing order.
    assert scores == sorted(scores, reverse=True)
    assert scores and scores[0] > 0
