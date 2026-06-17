"""Mode-invariance of shared panel fields (M1 regression guard)."""

from __future__ import annotations

import pytest

from panelapp_link.models.enums import RESPONSE_MODES
from panelapp_link.services import shaping

_ROW = {
    "panel_id": 283,
    "name": "Cystic kidney disease",
    "region": "uk",
    "number_of_genes": 80,
    "number_of_regions": 2,
    "number_of_strs": 0,
    "version": "9.1",
    "disease_group": "Renal",
    "disease_sub_group": "",
    "status": "public",
    "signed_off_version": "9.0",
    "signed_off_date": "2026-05-06",
    "relevant_disorders": ["Cystic kidney disease"],
    "version_created": "2026-05-06T16:02:21Z",
    "description": None,
    "types": [],
    "entity_counts": {"gene": 80, "region": 2, "str": 0},
}


@pytest.mark.parametrize("mode", RESPONSE_MODES)
def test_panel_count_fields_are_mode_invariant(mode: str) -> None:
    out = shaping.shape_panel(_ROW, mode)
    assert out["n_genes"] == 80
    assert out["n_regions"] == 2
    assert out["n_strs"] == 0
    assert "number_of_genes" not in out
    assert "number_of_regions" not in out
    assert "number_of_strs" not in out
