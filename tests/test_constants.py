"""Tests for panelapp_link.constants confidence maps and helpers."""

from __future__ import annotations

from panelapp_link.constants import (
    CONFIDENCE_RANK,
    CONFIDENCE_TO_LABEL,
    REGION_LABELS,
    confidence_label,
    confidence_rank_for_label,
)


def test_confidence_label_maps_levels() -> None:
    """3/4 -> green, 2 -> amber, 0/1 -> red."""
    assert confidence_label("4") == "green"
    assert confidence_label("3") == "green"
    assert confidence_label("2") == "amber"
    assert confidence_label("1") == "red"
    assert confidence_label("0") == "red"


def test_confidence_label_casts_to_str() -> None:
    """Integer-like input is cast to str before lookup."""
    assert confidence_label(str(3)) == "green"
    # Pass an int via str() to mirror how the builder casts API values.
    assert confidence_label(str(2)) == "amber"


def test_confidence_label_unknown_defaults_red() -> None:
    """Unknown / empty levels fall back to red."""
    assert confidence_label("99") == "red"
    assert confidence_label("") == "red"


def test_confidence_rank_ordering() -> None:
    """green > amber > red."""
    green = confidence_rank_for_label("green")
    amber = confidence_rank_for_label("amber")
    red = confidence_rank_for_label("red")
    assert green > amber > red
    assert (green, amber, red) == (3, 2, 1)


def test_confidence_rank_unknown_defaults_red_rank() -> None:
    """Unknown labels default to the red rank (1)."""
    assert confidence_rank_for_label("magenta") == 1


def test_confidence_maps_are_consistent() -> None:
    """Every label produced by the level map has a rank."""
    for label in set(CONFIDENCE_TO_LABEL.values()):
        assert label in CONFIDENCE_RANK


def test_region_labels() -> None:
    """Region labels are present."""
    assert REGION_LABELS["uk"] == "Genomics England PanelApp"
    assert REGION_LABELS["australia"] == "PanelApp Australia"
