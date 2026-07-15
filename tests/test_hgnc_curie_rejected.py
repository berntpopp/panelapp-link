"""Issue #25 D3: an HGNC CURIE is not a gene lookup key -- reject it, don't half-answer.

``resolve_gene(query='HGNC:10585')`` was passed straight to
``/genes/?entity_name=HGNC:10585``. PanelApp Australia loosely matches that to
SCN1A while Genomics England (UK) returns nothing, so the call succeeded with
``regions: ['australia']`` and silently dropped every UK panel -- "half-works",
which is worse than a clean miss. PanelApp is keyed by gene SYMBOL only (there is
no server-side HGNC lookup: an unknown ``?hgnc_id=`` filter is ignored and returns
the whole gene list). So an HGNC CURIE in the lookup position must be rejected
with ``invalid_input`` naming ``gene_symbol`` -- on both symbol-lookup tools, and
for the class of CURIE, not just the reported id.
"""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import InvalidInputError


@pytest.mark.parametrize("curie", ["HGNC:10585", "hgnc:20", "HGNC:1100", "HGNC:9008"])
async def test_resolve_gene_rejects_hgnc_curie(live_service, curie: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.resolve_gene(query=curie)
    assert exc.value.field == "gene_symbol"


@pytest.mark.parametrize("curie", ["HGNC:10585", "hgnc:20"])
async def test_get_gene_panels_rejects_hgnc_curie_as_symbol(live_service, curie: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_gene_panels(gene_symbol=curie)
    assert exc.value.field == "gene_symbol"


async def test_hgnc_id_filter_still_accepted_alongside_a_real_symbol(live_service) -> None:
    """The hgnc_id FILTER (a legit CURIE) must still work when a real symbol drives it."""
    # No InvalidInputError: hgnc_id here is the optional result filter, not the key.
    out = await live_service.get_gene_panels(gene_symbol="AAAS", hgnc_id="HGNC:13666")
    assert isinstance(out, dict) and "count" in out
