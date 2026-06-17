"""Unit tests for the pure live-service helpers (matching + ranking)."""

from __future__ import annotations

from panelapp_link.services import _live_helpers as helpers


def test_score_word_prefix_not_substring() -> None:
    # "renal" is a mid-word substring of "Adrenal" -> must NOT match.
    assert helpers.panel_match_score({"name": "Adrenal insufficiency"}, "renal") == 0
    # whole-word prefix match on the name field -> weight 3.
    assert helpers.panel_match_score({"name": "Renal ciliopathies"}, "renal") == 3


def test_score_field_weighting() -> None:
    by_name = {"name": "Cystic kidney disease"}
    by_disorder = {"name": "X", "relevant_disorders": ["cystic kidney disease"]}
    by_group = {"name": "X", "disease_group": "Cystic disorders"}
    assert helpers.panel_match_score(by_name, "cystic") == 3
    assert helpers.panel_match_score(by_disorder, "cystic") == 2
    assert helpers.panel_match_score(by_group, "cystic") == 1


def test_score_multi_token_requires_all_in_one_field() -> None:
    panel = {"name": "Cystic kidney disease"}
    assert helpers.panel_match_score(panel, "cystic kidney") == 3
    assert helpers.panel_match_score(panel, "cystic lung") == 0


def test_score_prefix_match_on_partial_token() -> None:
    # A query token is a *prefix* of a whole word -> matches ("cyst" -> "Cystic").
    assert helpers.panel_match_score({"name": "Cystic kidney disease"}, "cyst") == 3
    # ...but never a mid-word or suffix match ("ystic" is not a prefix of any word).
    assert helpers.panel_match_score({"name": "Cystic kidney disease"}, "ystic") == 0


def test_panel_matches_is_score_gt_zero() -> None:
    assert helpers.panel_matches({"name": "Renal ciliopathies"}, "renal") is True
    assert helpers.panel_matches({"name": "Adrenal insufficiency"}, "renal") is False


def test_rank_panels_orders_name_match_first() -> None:
    rows = [
        {"name": "Z disorder", "relevant_disorders": ["cystic kidney"], "region": "uk"},
        {"name": "Cystic kidney disease", "relevant_disorders": [], "region": "uk"},
    ]
    ranked = helpers.rank_panels(rows, "cystic")
    assert ranked[0]["name"] == "Cystic kidney disease"


def test_rank_panels_empty_needle_is_alphabetical() -> None:
    rows = [{"name": "B", "region": "uk"}, {"name": "A", "region": "uk"}]
    assert [r["name"] for r in helpers.rank_panels(rows, "")] == ["A", "B"]


def test_rank_panels_is_stable_within_equal_scores() -> None:
    # Equal-score rows fall back to (name, region); ranking never drops rows.
    rows = [
        {"name": "Acute B", "region": "uk"},
        {"name": "Acute A", "region": "australia"},
        {"name": "Acute A", "region": "uk"},
    ]
    ranked = helpers.rank_panels(rows, "acute")
    assert [(r["name"], r["region"]) for r in ranked] == [
        ("Acute A", "australia"),
        ("Acute A", "uk"),
        ("Acute B", "uk"),
    ]
    assert len(ranked) == len(rows)
