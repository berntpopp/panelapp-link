"""WS-3: panels_for_genes batch membership with per-symbol error isolation."""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import DownloadError, NotFoundError
from panelapp_link.services import aggregations


class _StubSvc:
    def __init__(self, known: dict[str, dict], fail: set[str] | None = None) -> None:
        self._known = known
        self._fail = fail or set()

    async def get_gene_panels(
        self,
        gene_symbol=None,
        hgnc_id=None,
        region="both",
        min_confidence=None,
        response_mode="compact",
    ) -> dict:
        sym = (gene_symbol or "").upper()
        if sym in self._fail:
            raise DownloadError("upstream 503", status_code=503)
        if sym not in self._known:
            raise NotFoundError(f"No PanelApp gene found for {sym!r}.")
        return self._known[sym]


def _gene_payload(sym: str, count: int, label: str) -> dict:
    return {
        "gene": {"gene_symbol": sym, "panel_count": count, "max_confidence_label": label},
        "panels": [{"panel_id": 1, "region": "uk", "confidence_label": label}],
    }


async def test_mixed_found_and_not_found() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")})
    out = await aggregations.panels_for_genes(svc, ["PKD1", "NOPE"], region="both")
    assert out["genes"]["PKD1"]["panel_count"] == 19
    assert out["genes"]["PKD1"]["max_confidence_label"] == "green"
    assert out["not_found"] == ["NOPE"]


async def test_operational_error_fails_whole_batch() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")}, fail={"BOOM"})
    with pytest.raises(DownloadError):
        await aggregations.panels_for_genes(svc, ["PKD1", "BOOM"])


async def test_cap_truncates_with_note() -> None:
    svc = _StubSvc({})
    symbols = [f"G{i}" for i in range(25)]
    out = await aggregations.panels_for_genes(svc, symbols, cap=20)
    assert out["truncated"]["requested"] == 25
    assert out["truncated"]["processed"] == 20
    assert len(out["not_found"]) == 20


async def test_minimal_mode_omits_panels() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")})
    out = await aggregations.panels_for_genes(svc, ["PKD1"], response_mode="minimal")
    assert "panels" not in out["genes"]["PKD1"]
    assert out["genes"]["PKD1"]["panel_count"] == 19
