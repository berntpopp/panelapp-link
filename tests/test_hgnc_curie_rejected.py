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

from panelapp_link.exceptions import InvalidInputError, NotFoundError


@pytest.mark.parametrize("curie", ["HGNC:10585", "hgnc:20", "HGNC:1100", "HGNC:9008"])
async def test_resolve_gene_rejects_hgnc_curie(live_service, curie: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.resolve_gene(query=curie)
    # Names the resolve_gene tool's ACTUAL param (query), not the nonexistent gene_symbol.
    assert exc.value.field == "query"


async def test_resolve_gene_rejects_hgnc_curie_via_gene_symbol_names_that_param(
    live_service,
) -> None:
    """The service also accepts gene_symbol; then the field names gene_symbol."""
    with pytest.raises(InvalidInputError) as exc:
        await live_service.resolve_gene(gene_symbol="HGNC:20")
    assert exc.value.field == "gene_symbol"


@pytest.mark.parametrize("curie", ["HGNC:10585", "hgnc:20"])
async def test_get_gene_panels_rejects_hgnc_curie_as_symbol(live_service, curie: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_gene_panels(gene_symbol=curie)
    assert exc.value.field == "gene_symbol"


async def test_hgnc_id_filter_still_accepted_alongside_a_real_symbol(live_service) -> None:
    """The hgnc_id FILTER (a matching CURIE) must still work when a real symbol drives it."""
    # No error: hgnc_id here is the optional result filter, and it matches AAAS.
    out = await live_service.get_gene_panels(gene_symbol="AAAS", hgnc_id="HGNC:13666")
    assert isinstance(out, dict) and out["count"] >= 1


async def test_hgnc_id_that_matches_nothing_is_not_found_not_silent_empty(live_service) -> None:
    """#25 D5 silent-empty: a well-formed hgnc_id matching no entity must fail loudly."""
    with pytest.raises(NotFoundError):
        await live_service.get_gene_panels(gene_symbol="AAAS", hgnc_id="HGNC:999999")


async def test_malformed_hgnc_id_filter_is_invalid_input(live_service) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await live_service.get_gene_panels(gene_symbol="AAAS", hgnc_id="not-a-curie")
    assert exc.value.field == "hgnc_id"


async def test_hgnc_id_filter_match_is_case_insensitive(live_service) -> None:
    """#25 rework: hgnc:13666 (lowercased) must match the stored HGNC:13666, not 404."""
    out = await live_service.get_gene_panels(gene_symbol="AAAS", hgnc_id="hgnc:13666")
    assert out["count"] >= 1
