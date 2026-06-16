"""PanelApp service: business logic over the read-only repository.

Tools call this layer (never the repository directly). Methods return plain,
JSON-ready dicts -- the data *payload* only. The MCP tool wrapper adds the
``_meta`` block, ``next_commands``, and the success/error envelope.

Region handling is centralized here: ``region="both"`` fans out to
``["uk", "australia"]`` and results are merged (deduped by ``(region, panel_id)``
for panels). ``min_confidence`` (a traffic-light label) maps to a numeric rank
floor via :data:`panelapp_link.constants.CONFIDENCE_RANK`. Cursor paging uses an
opaque base64(JSON ``{"offset": N}``) token, mirroring the fleet contract.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from panelapp_link.constants import CONFIDENCE_RANK
from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.exceptions import (
    AmbiguousQueryError,
    DataUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from panelapp_link.models.enums import ENTITY_TYPES, RESPONSE_MODES, ResponseMode
from panelapp_link.services import shaping

_MAX_LIMIT = 500

# Cap on rows pulled from the repository for a search before dedupe/slice. Both
# regions together are < 1000 panels, so this lets the service report an exact
# deduped ``total`` and page deterministically without a repo-side COUNT.
_SEARCH_FETCH_CAP = 2000

# region argument -> repository region keys.
_REGION_MAP: dict[str, list[str]] = {
    "both": ["uk", "australia"],
    "uk": ["uk"],
    "australia": ["australia"],
}

_TRUNCATION_HINT = (
    "More results available; re-call with next_offset, or follow next_cursor for paging."
)


class _TTLCache:
    """Tiny insertion-ordered TTL cache (disabled when maxsize <= 0)."""

    def __init__(self, maxsize: int, ttl: int) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        if self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self._maxsize <= 0:
            return
        if len(self._store) >= self._maxsize:
            self._store.pop(next(iter(self._store)), None)
        self._store[key] = (time.monotonic() + self._ttl, value)


def _encode_cursor(offset: int) -> str:
    """Encode an opaque, url-safe ``{"offset": N}`` cursor (no padding)."""
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    """Decode a cursor token to its offset; raise ``InvalidInputError`` on garbage."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        offset = int(payload["offset"])
    except Exception as exc:  # malformed base64 / json / missing key
        raise InvalidInputError("cursor is malformed.", field="cursor") from exc
    if offset < 0:
        raise InvalidInputError("cursor offset is invalid.", field="cursor")
    return offset


class PanelAppService:
    """Read-only business logic over the PanelApp SQLite index."""

    def __init__(
        self,
        repository: PanelAppRepository,
        *,
        cache_size: int = 512,
        cache_ttl: int = 3600,
    ) -> None:
        self._repo = repository
        self._cache = _TTLCache(cache_size, cache_ttl)

    # --- helpers --------------------------------------------------------

    @staticmethod
    def _normalize_region(region: str) -> list[str]:
        """Map a ``region`` argument to repository region keys.

        ``"both"`` -> ``["uk", "australia"]``; ``"uk"``/``"australia"`` ->
        single-element lists. Anything else raises ``InvalidInputError``.
        """
        keys = _REGION_MAP.get(region)
        if keys is None:
            raise InvalidInputError(
                f"Invalid region {region!r}. Use 'uk', 'australia', or 'both'.",
                field="region",
            )
        return list(keys)

    @staticmethod
    def _validate_mode(mode: str) -> ResponseMode:
        if mode not in RESPONSE_MODES:
            raise InvalidInputError(
                f"Invalid response_mode {mode!r}. Use one of: {', '.join(RESPONSE_MODES)}.",
                field="response_mode",
            )
        return mode

    @staticmethod
    def _validate_entity_type(entity_type: str) -> str:
        if entity_type not in ENTITY_TYPES:
            raise InvalidInputError(
                f"Invalid entity_type {entity_type!r}. Use one of: {', '.join(ENTITY_TYPES)}.",
                field="entity_type",
            )
        return entity_type

    @staticmethod
    def _min_rank(min_confidence: str | None) -> int | None:
        """Map a min_confidence label to a numeric rank floor, validating it."""
        if min_confidence is None:
            return None
        rank = CONFIDENCE_RANK.get(min_confidence)
        if rank is None:
            raise InvalidInputError(
                f"Invalid min_confidence {min_confidence!r}. Use one of: "
                f"{', '.join(CONFIDENCE_RANK)}.",
                field="min_confidence",
            )
        return rank

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        if limit < 1:
            raise InvalidInputError("limit must be >= 1.", field="limit")
        return min(limit, _MAX_LIMIT)

    @staticmethod
    def _validate_offset(offset: int) -> int:
        if offset < 0:
            raise InvalidInputError("offset must be >= 0.", field="offset")
        return offset

    @staticmethod
    def _truncation(total: int, limit: int, offset: int, returned: int) -> dict[str, Any] | None:
        """Return a truncation block (with next_offset + next_cursor) when more exist."""
        if offset + returned >= total:
            return None
        next_offset = offset + returned
        return {
            "total": total,
            "returned": returned,
            "next_offset": next_offset,
            "next_cursor": _encode_cursor(next_offset),
            "hint": _TRUNCATION_HINT,
        }

    @staticmethod
    def _coalesce_gene(
        gene_symbol: str | None,
        hgnc_id: str | None,
        query: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Coalesce gene inputs to ``(gene_symbol_upper, hgnc_id)``.

        Precedence: explicit ``hgnc_id`` > explicit ``gene_symbol`` > free-text
        ``query`` (an ``HGNC:`` prefix routes to hgnc_id, else a symbol). Raises
        ``InvalidInputError`` when nothing usable is supplied.
        """
        hid = (hgnc_id or "").strip() or None
        sym = (gene_symbol or "").strip() or None
        if hid is None and sym is None and query:
            q = query.strip()
            if q.upper().startswith("HGNC:"):
                hid = q
            elif q:
                sym = q
        if hid is None and sym is None:
            raise InvalidInputError(
                "Provide a gene_symbol or hgnc_id (or a non-empty query).",
                field="gene_symbol",
            )
        return (sym.upper() if sym else None, hid)

    # --- search ---------------------------------------------------------

    def search_panels(
        self,
        query: str = "",
        region: str = "both",
        response_mode: str = "compact",
        limit: int = 20,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search panels by name/disorders/disease group, merged across regions.

        Returns ``{"query","count","total","panels":[...],"truncated"?}``. Panels
        are deduped by ``(region, panel_id)``; paging is over the deduped set.
        """
        if cursor is not None:
            offset = _decode_cursor(cursor)
        mode = self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        limit = self._clamp_limit(limit)
        offset = self._validate_offset(offset)
        q = (query or "").strip()

        rows = self._repo.search_panels(q, regions, _SEARCH_FETCH_CAP, 0)
        seen: set[tuple[str, int]] = set()
        deduped: list[dict[str, Any]] = []
        for row in rows:
            key = (row["region"], row["panel_id"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        total = len(deduped)
        page = deduped[offset : offset + limit]
        payload: dict[str, Any] = {
            "query": q,
            "count": len(page),
            "total": total,
            "panels": [shaping.shape_panel(r, mode) for r in page],
        }
        trunc = self._truncation(total, limit, offset, len(page))
        if trunc:
            payload["truncated"] = trunc
        return payload

    # --- panel detail ---------------------------------------------------

    def get_panel(
        self,
        panel_id: int,
        region: str,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        """Return one panel's detail (+ entity count breakdown).

        ``region`` must be ``"uk"`` or ``"australia"`` (panel ids are per-region;
        ``"both"`` is rejected). Raises ``NotFoundError`` when absent.
        """
        mode = self._validate_mode(response_mode)
        if region == "both":
            raise InvalidInputError(
                "region must be 'uk' or 'australia' for get_panel (panel ids are "
                "per-region; 'both' is not allowed).",
                field="region",
            )
        regions = self._normalize_region(region)
        row = self._repo.get_panel(regions[0], panel_id)
        if row is None:
            raise NotFoundError(
                f"No PanelApp panel {panel_id} in region {region!r}. "
                "Try search_panels to find a panel id."
            )
        return {"panel": shaping.shape_panel(row, mode)}

    def get_panel_genes(
        self,
        panel_id: int,
        region: str,
        entity_type: str = "gene",
        min_confidence: str | None = None,
        response_mode: str = "compact",
        limit: int = 100,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return a panel's entities, filtered by type and minimum confidence.

        Returns ``{"panel_id","region","entity_type","count","total",
        "entities":[...],"truncated"?}``.
        """
        if cursor is not None:
            offset = _decode_cursor(cursor)
        mode = self._validate_mode(response_mode)
        if region == "both":
            raise InvalidInputError(
                "region must be 'uk' or 'australia' for get_panel_genes.",
                field="region",
            )
        regions = self._normalize_region(region)
        entity_type = self._validate_entity_type(entity_type)
        min_rank = self._min_rank(min_confidence)
        limit = self._clamp_limit(limit)
        offset = self._validate_offset(offset)
        region_key = regions[0]

        # One extra row tells us whether a further page exists without a COUNT.
        rows = self._repo.get_panel_entities(
            region_key, panel_id, entity_type, min_rank, limit + 1, offset
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        total = offset + len(page) + (1 if has_more else 0)
        payload: dict[str, Any] = {
            "panel_id": panel_id,
            "region": region_key,
            "entity_type": entity_type,
            "count": len(page),
            "total": total,
            "entities": [shaping.shape_entity(r, mode) for r in page],
        }
        if has_more:
            next_offset = offset + len(page)
            payload["truncated"] = {
                "total": total,
                "returned": len(page),
                "next_offset": next_offset,
                "next_cursor": _encode_cursor(next_offset),
                "hint": _TRUNCATION_HINT,
            }
        return payload

    # --- gene -> panels -------------------------------------------------

    def get_gene_panels(
        self,
        gene_symbol: str | None = None,
        hgnc_id: str | None = None,
        region: str = "both",
        min_confidence: str | None = None,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        """Return the panels a gene appears on, across regions, sorted by confidence.

        Returns ``{"gene","count","panels":[...]}`` where ``panels`` are shaped
        ``GenePanelHit`` rows ordered by confidence rank (desc) then region.
        Raises ``NotFoundError`` when the gene is absent.
        """
        self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        min_rank = self._min_rank(min_confidence)
        gene_upper, hid = self._coalesce_gene(gene_symbol, hgnc_id)

        gene_rows = self._repo.resolve_gene(gene_symbol_upper=gene_upper, hgnc_id=hid)
        if not gene_rows:
            ident = hid or gene_upper
            raise NotFoundError(
                f"No PanelApp gene found for {ident!r}. Try resolve_gene to confirm a symbol."
            )
        if len({r["gene_symbol_upper"] for r in gene_rows}) > 1:
            candidates = sorted({r["gene_symbol"] for r in gene_rows})
            raise AmbiguousQueryError(
                f"{(hid or gene_upper)!r} matches multiple genes: {', '.join(candidates)}. "
                "Re-run get_gene_panels with a specific gene_symbol.",
                candidates=candidates,
            )
        gene = gene_rows[0]

        hits = self._repo.get_gene_panels(
            gene_symbol_upper=gene["gene_symbol_upper"],
            hgnc_id=None,
            regions=regions,
            min_rank=min_rank,
        )
        hits.sort(
            key=lambda h: (-(h.get("confidence_rank") or 0), h.get("region") or ""),
        )
        return {
            "gene": shaping.shape_gene(gene),
            "count": len(hits),
            "panels": [shaping.shape_gene_panel_hit(h) for h in hits],
        }

    def resolve_gene(
        self,
        query: str | None = None,
        gene_symbol: str | None = None,
        hgnc_id: str | None = None,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        """Resolve free-text / symbol / hgnc id to a single rolled-up gene.

        Returns ``{"query","gene","matches":[...]}``. Raises ``NotFoundError``
        when nothing matches and ``AmbiguousQueryError`` when an hgnc id maps to
        more than one distinct gene symbol.
        """
        self._validate_mode(response_mode)
        gene_upper, hid = self._coalesce_gene(gene_symbol, hgnc_id, query)
        resolved_query = hid or gene_upper

        matches = self._repo.resolve_gene(gene_symbol_upper=gene_upper, hgnc_id=hid)
        if not matches:
            raise NotFoundError(
                f"Could not resolve {resolved_query!r} to a PanelApp gene. "
                "Try search_panels to discover panels first."
            )
        distinct = {m["gene_symbol_upper"] for m in matches}
        if len(distinct) > 1:
            candidates = sorted({m["gene_symbol"] for m in matches})
            raise AmbiguousQueryError(
                f"{resolved_query!r} matches multiple genes: {', '.join(candidates)}.",
                candidates=candidates,
            )
        shaped = [shaping.shape_gene(m) for m in matches]
        return {
            "query": resolved_query,
            "gene": shaped[0],
            "matches": shaped,
        }

    # --- discovery ------------------------------------------------------

    def capabilities_data(self) -> dict[str, Any]:
        """Return live data freshness for capabilities, degrading gracefully.

        On a missing/unbuilt database returns ``{"status": "data_unavailable"}``
        rather than raising, so capabilities stays answerable.
        """
        try:
            meta = self._repo.get_meta()
        except DataUnavailableError:
            return {"status": "data_unavailable"}
        return {
            "status": "ok",
            "schema_version": meta.get("schema_version"),
            "uk_panel_count": meta.get("uk_panel_count", 0),
            "au_panel_count": meta.get("au_panel_count", 0),
            "entity_count": meta.get("entity_count", 0),
            "gene_count": meta.get("gene_count", 0),
            "build_utc": meta.get("build_utc"),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return full build provenance from ``meta`` (raises if unavailable)."""
        return self._repo.get_meta()
