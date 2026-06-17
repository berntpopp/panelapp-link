"""WS-2: compare_panels gene-level diff over a stub service (deterministic)."""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import InvalidInputError
from panelapp_link.services import aggregations


class _StubSvc:
    """Minimal stand-in exposing get_panel + get_panel_genes."""

    def __init__(self, genes_by_ref: dict[tuple[int, str], list[dict]]) -> None:
        self._genes = genes_by_ref

    async def get_panel(self, panel_id: int, region: str, response_mode: str = "compact") -> dict:
        ents = self._genes[(panel_id, region)]
        return {
            "panel": {
                "panel_id": panel_id,
                "region": region,
                "name": f"P{panel_id}",
                "n_genes": len(ents),
            }
        }

    async def get_panel_genes(
        self,
        panel_id,
        region,
        entity_type="gene",
        min_confidence=None,
        response_mode="compact",
        cursor=None,
    ) -> dict:
        return {"entities": list(self._genes[(panel_id, region)])}


def _g(symbol: str, label: str) -> dict:
    return {"gene_symbol": symbol, "entity_name": symbol, "confidence_label": label}


async def test_self_compare_is_full_overlap() -> None:
    svc = _StubSvc({(1, "uk"): [_g("A", "green"), _g("B", "amber")]})
    out = await aggregations.compare_panels(
        svc, [{"panel_id": 1, "region": "uk"}, {"panel_id": 1, "region": "uk"}]
    )
    assert out["shared"] == ["A", "B"]
    assert out["only_in"] == {"1@uk": []}
    assert out["summary"] == {"n_shared": 2, "n_union": 2}


async def test_two_panel_union_math_and_deltas() -> None:
    svc = _StubSvc(
        {
            (1, "uk"): [_g("A", "green"), _g("B", "amber")],
            (2, "uk"): [_g("A", "amber"), _g("C", "green")],
        }
    )
    out = await aggregations.compare_panels(
        svc, [{"panel_id": 1, "region": "uk"}, {"panel_id": 2, "region": "uk"}]
    )
    assert out["shared"] == ["A"]
    assert out["only_in"]["1@uk"] == ["B"]
    assert out["only_in"]["2@uk"] == ["C"]
    assert out["summary"] == {"n_shared": 1, "n_union": 3}
    assert out["confidence_deltas"] == [
        {"gene_symbol": "A", "per_panel": {"1@uk": "green", "2@uk": "amber"}}
    ]


async def test_rejects_fewer_than_two_or_region_both() -> None:
    svc = _StubSvc({(1, "uk"): []})
    with pytest.raises(InvalidInputError):
        await aggregations.compare_panels(svc, [{"panel_id": 1, "region": "uk"}])
    with pytest.raises(InvalidInputError):
        await aggregations.compare_panels(
            svc, [{"panel_id": 1, "region": "both"}, {"panel_id": 2, "region": "uk"}]
        )
