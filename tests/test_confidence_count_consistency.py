"""Issue #25 D1: min_confidence must be reflected by the gene roll-up count.

The bug: ``get_gene_panels`` / ``get_panels_for_genes`` reported the gene's
``panel_count`` from the UNFILTERED result set (``len(results)``) while the
``panels`` array beside it was the FILTERED hit set -- so a filtered call said
``panel_count: 13`` next to a 10-element panels array (and matched the unfiltered
call). The count must reflect the filter, and it must equal the panels array it
sits next to and the sibling ``get_gene_panels`` count.

These tests are written against the CORRECT behaviour and fail against the buggy
``gene_identity`` (which keyed panel_count/regions off ``results``, not ``hits``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from panelapp_link.api.client import PanelAppRestClient
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.services import aggregations
from panelapp_link.services.panelapp_service import PanelAppService

UK_BASE = "https://uk.panelapp.test/api/v1"
AU_BASE = "https://au.panelapp.test/api/v1"


def _page(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"count": len(results), "next": None, "previous": None, "results": results}


def _hit(panel_id: int, level: str) -> dict[str, Any]:
    """A /genes/?entity_name= result: entity + confidence + its panel."""
    return {
        "entity_type": "gene",
        "entity_name": "MIXED",
        "confidence_level": level,
        "gene_data": {"gene_symbol": "MIXED", "hgnc_id": "HGNC:99"},
        "panel": {"id": panel_id, "name": f"Panel {panel_id}", "version": "1.0"},
        "mode_of_inheritance": "BIALLELIC",
    }


# MIXED sits on 3 UK panels: two GREEN (level 3/4) and one RED (level 1).
_MIXED_GENES = _page([_hit(11, "3"), _hit(22, "4"), _hit(33, "1")])


def _config() -> PanelAppDataConfigModel:
    return PanelAppDataConfigModel(
        uk_api_url=UK_BASE, au_api_url=AU_BASE, max_retries=1, max_concurrency=2, request_timeout=5
    )


@pytest.fixture
async def service() -> AsyncIterator[PanelAppService]:
    router = respx.mock(assert_all_called=False, base_url=None)
    for base in (UK_BASE, AU_BASE):
        router.get(f"{base}/panels/").mock(return_value=httpx.Response(200, json=_page([])))
        router.get(f"{base}/panels/signedoff/").mock(
            return_value=httpx.Response(200, json=_page([]))
        )

    def _genes(request: httpx.Request) -> httpx.Response:
        # UK knows MIXED; AU knows nothing (keeps the roll-up single-region).
        if str(request.url).startswith(UK_BASE) and "MIXED" in str(request.url):
            return httpx.Response(200, json=_MIXED_GENES)
        return httpx.Response(200, json=_page([]))

    router.get(url__startswith=f"{UK_BASE}/genes/").mock(side_effect=_genes)
    router.get(url__startswith=f"{AU_BASE}/genes/").mock(side_effect=_genes)
    transport = httpx.MockTransport(router.async_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = PanelAppRestClient(_config(), client=http_client)
        yield PanelAppService(client, _config(), cache_ttl=3600, cache_size=64)


async def test_get_gene_panels_count_matches_panel_count_and_array_under_filter(
    service: PanelAppService,
) -> None:
    """panel_count == count == len(panels), all filtered to green."""
    green = await service.get_gene_panels(gene_symbol="MIXED", region="uk", min_confidence="green")
    assert green["count"] == 2, "two green panels pass the filter"
    assert green["gene"]["panel_count"] == 2, (
        "roll-up count must reflect the filter, not len(results)"
    )
    assert len(green["panels"]) == 2
    assert green["gene"]["regions"] == ["uk"]
    assert green["gene"]["max_confidence_label"] == "green"
    assert all(p["confidence_label"] == "green" for p in green["panels"])


async def test_get_gene_panels_unfiltered_counts_all(service: PanelAppService) -> None:
    unf = await service.get_gene_panels(gene_symbol="MIXED", region="uk")
    assert unf["count"] == 3
    assert unf["gene"]["panel_count"] == 3
    assert len(unf["panels"]) == 3


async def test_get_panels_for_genes_count_equals_panels_len_under_filter(
    service: PanelAppService,
) -> None:
    """DoD (a): batch panel_count == its panels array length == get_gene_panels count."""
    batch = await aggregations.panels_for_genes(
        service, ["MIXED"], region="uk", min_confidence="green"
    )
    entry = batch["genes"]["MIXED"]
    assert entry["panel_count"] == 2
    assert len(entry["panels"]) == 2
    assert entry["panel_count"] == len(entry["panels"])

    gp = await service.get_gene_panels(gene_symbol="MIXED", region="uk", min_confidence="green")
    assert entry["panel_count"] == gp["count"]
