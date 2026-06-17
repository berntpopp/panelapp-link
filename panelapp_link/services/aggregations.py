"""Cross-panel / cross-gene aggregation orchestration.

Free functions that compose the *public* PanelAppService methods (so the
line-tight service body stays frozen) into higher-order, token-saving views:
``compare_panels`` (gene-level diff) and ``panels_for_genes`` (batch membership).
All fan-out rides the service's cache + concurrency-capped client.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from panelapp_link.exceptions import InvalidInputError, NotFoundError

_MIN_PANELS = 2
_MAX_PANELS = 5


class _Service(Protocol):
    async def get_panel(
        self, panel_id: int, region: str, response_mode: str = ...
    ) -> dict[str, Any]: ...
    async def get_panel_genes(
        self,
        panel_id: int,
        region: str,
        entity_type: str = ...,
        min_confidence: str | None = ...,
        response_mode: str = ...,
        limit: int = ...,
        offset: int = ...,
        cursor: str | None = ...,
    ) -> dict[str, Any]: ...
    async def get_gene_panels(
        self,
        gene_symbol: str | None = ...,
        hgnc_id: str | None = ...,
        region: str = ...,
        min_confidence: str | None = ...,
        response_mode: str = ...,
    ) -> dict[str, Any]: ...


def _ref_key(ref: dict[str, Any]) -> str:
    return f"{ref['panel_id']}@{ref['region']}"


def _validate_refs(panel_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not (_MIN_PANELS <= len(panel_refs) <= _MAX_PANELS):
        raise InvalidInputError(
            f"compare_panels needs {_MIN_PANELS}-{_MAX_PANELS} panels.", field="panels"
        )
    out: list[dict[str, Any]] = []
    for ref in panel_refs:
        region = ref.get("region")
        panel_id = ref.get("panel_id")
        if region not in ("uk", "australia"):
            raise InvalidInputError(
                "region must be 'uk' or 'australia' per panel (panel ids are "
                "per-region; 'both' is not allowed).",
                field="region",
            )
        if not isinstance(panel_id, int):
            raise InvalidInputError("panel_id must be an integer.", field="panel_id")
        out.append({"panel_id": panel_id, "region": region})
    return out


async def _all_genes(
    svc: _Service, panel_id: int, region: str, min_confidence: str | None, response_mode: str
) -> list[dict[str, Any]]:
    """Page through every gene entity of a panel (panel detail is cached after page 1)."""
    entities: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        res = await svc.get_panel_genes(
            panel_id,
            region,
            entity_type="gene",
            min_confidence=min_confidence,
            response_mode=response_mode,
            cursor=cursor,
        )
        entities.extend(res.get("entities", []))
        cursor = (res.get("truncated") or {}).get("next_cursor")
        if not cursor:
            return entities


async def compare_panels(
    svc: _Service,
    panel_refs: list[dict[str, Any]],
    *,
    min_confidence: str | None = None,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Diff genes across 2-5 panels: shared / only-in / confidence deltas."""
    refs = _validate_refs(panel_refs)
    metas, gene_lists = await asyncio.gather(
        asyncio.gather(*(svc.get_panel(r["panel_id"], r["region"], "compact") for r in refs)),
        asyncio.gather(
            *(
                _all_genes(svc, r["panel_id"], r["region"], min_confidence, response_mode)
                for r in refs
            )
        ),
    )

    keys = [_ref_key(r) for r in refs]
    by_symbol: dict[str, dict[str, str | None]] = {}
    per_ref_symbols: list[set[str]] = []
    for key, ents in zip(keys, gene_lists, strict=True):
        symbols: set[str] = set()
        for ent in ents:
            sym = ent.get("gene_symbol")
            if not sym:
                continue
            symbols.add(sym)
            by_symbol.setdefault(sym, {})[key] = ent.get("confidence_label")
        per_ref_symbols.append(symbols)

    union = sorted(set().union(*per_ref_symbols)) if per_ref_symbols else []
    shared = sorted(s for s in union if all(s in ps for ps in per_ref_symbols))
    only_in = {
        key: sorted(s for s in ps if not all(s in other for other in per_ref_symbols))
        for key, ps in zip(keys, per_ref_symbols, strict=True)
    }

    panels_out: list[dict[str, Any]] = []
    for meta, ps in zip(metas, per_ref_symbols, strict=True):
        panel = meta.get("panel") or {}
        if response_mode == "minimal":
            panels_out.append({"panel_id": panel.get("panel_id"), "region": panel.get("region")})
        else:
            panels_out.append(
                {
                    "panel_id": panel.get("panel_id"),
                    "region": panel.get("region"),
                    "name": panel.get("name"),
                    "n_genes": len(ps),
                }
            )

    out: dict[str, Any] = {
        "panels": panels_out,
        "shared": shared,
        "only_in": only_in,
        "summary": {"n_shared": len(shared), "n_union": len(union)},
    }
    if response_mode == "minimal":
        return out

    if response_mode in ("standard", "full"):
        out["confidence_deltas"] = [{"gene_symbol": s, "per_panel": by_symbol[s]} for s in shared]
    else:  # compact: only genes whose label differs across panels
        out["confidence_deltas"] = [
            {"gene_symbol": s, "per_panel": by_symbol[s]}
            for s in shared
            if len({by_symbol[s].get(k) for k in keys}) > 1
        ]
    return out


async def panels_for_genes(
    svc: _Service,
    gene_symbols: list[str],
    *,
    region: str = "both",
    min_confidence: str | None = None,
    response_mode: str = "compact",
    cap: int = 20,
) -> dict[str, Any]:
    """Batch gene->panel membership with per-symbol NotFound isolation.

    Unknown symbols collect into ``not_found``; operational errors (download /
    rate-limit) propagate and fail the whole call (retryable envelope upstream).
    """
    cleaned = [s.strip().upper() for s in gene_symbols if s and s.strip()]
    deduped = list(dict.fromkeys(cleaned))  # order-preserving unique
    if not deduped:
        raise InvalidInputError("Provide at least one gene_symbol.", field="gene_symbols")
    processed = deduped[:cap]

    async def _one(symbol: str) -> tuple[str, dict[str, Any] | None]:
        try:
            res = await svc.get_gene_panels(
                gene_symbol=symbol,
                region=region,
                min_confidence=min_confidence,
                response_mode=response_mode,
            )
            return symbol, res
        except NotFoundError:
            return symbol, None

    # DownloadError / RateLimitError propagate out of gather -> envelope fails.
    results = await asyncio.gather(*(_one(s) for s in processed))

    genes: dict[str, Any] = {}
    not_found: list[str] = []
    for symbol, res in results:
        if res is None:
            not_found.append(symbol)
            continue
        gene = res.get("gene") or {}
        entry: dict[str, Any] = {
            "panel_count": gene.get("panel_count", 0),
            "max_confidence_label": gene.get("max_confidence_label"),
        }
        if response_mode != "minimal":
            entry["panels"] = res.get("panels", [])
        genes[symbol] = entry

    out: dict[str, Any] = {"genes": genes, "not_found": not_found}
    if len(deduped) > cap:
        out["truncated"] = {
            "requested": len(deduped),
            "processed": cap,
            "hint": f"cap is {cap} symbols per call; resubmit the remaining {len(deduped) - cap}.",
        }
    return out
