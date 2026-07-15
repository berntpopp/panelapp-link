"""Issue #25 D4: a non-positive panel_id must be rejected, never queried.

``get_panel(panel_id=-1, region='uk')`` returned a real, unrelated panel
(COVID-19 research, id 111) with ``success: true`` -- a classic negative-index
leak: the id is interpolated straight into ``/panels/{panel_id}/`` and PanelApp
answered ``-1`` with some other panel, while ``-2``/``-3`` 404 (not_found). A
well-formed-but-invalid id must fail with ``invalid_input`` naming ``panel_id``,
mirroring the existing ``limit >= 1`` guard -- on EVERY panel_id path, not just
the reported ``get_panel``.
"""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import InvalidInputError
from panelapp_link.services import aggregations


@pytest.mark.parametrize("bad_id", [-1, 0, -2, -5])
async def test_get_panel_rejects_non_positive_id(live_service, bad_id: int) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel(bad_id, "uk")
    assert exc.value.field == "panel_id"


@pytest.mark.parametrize("bad_id", [-1, 0])
async def test_get_panel_genes_rejects_non_positive_id(live_service, bad_id: int) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_panel_genes(bad_id, "uk")
    assert exc.value.field == "panel_id"


async def test_compare_panels_rejects_non_positive_ref_id(live_service) -> None:
    """The class, one level up: a -1 ref must be rejected before any fetch."""
    with pytest.raises(InvalidInputError) as exc:
        await aggregations.compare_panels(
            live_service,
            [{"panel_id": -1, "region": "uk"}, {"panel_id": 285, "region": "uk"}],
        )
    assert exc.value.field == "panel_id"
