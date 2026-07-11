"""PanelApp service: live business logic over the PanelApp REST APIs.

Tools call this layer (never the REST client directly). All public methods are
``async`` and return plain, JSON-ready dicts -- the data *payload* only. The MCP
tool wrapper adds the ``_meta`` block, ``next_commands``, and the success/error
envelope.

There is no local database: each query calls the live PanelApp API (1-2 calls)
and memoizes the raw payloads in a small in-memory TTL cache so repeated/related
queries within the TTL window do not re-hit the upstream. The full panel list per
region is cheap (summaries only) and is filtered in memory because PanelApp has
no usable server-side panel search.

Region handling is centralized here: ``region="both"`` fans out to
``["uk", "australia"]`` and results are merged (deduped by ``(region, panel_id)``
for panels). ``min_confidence`` (a traffic-light label) maps to a numeric rank
floor via :data:`panelapp_link.constants.CONFIDENCE_RANK`. Cursor paging uses an
opaque base64(JSON ``{"offset": N}``) token, mirroring the fleet contract.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from panelapp_link.config import get_data_config
from panelapp_link.constants import CONFIDENCE_RANK
from panelapp_link.exceptions import (
    DownloadError,
    InvalidInputError,
    NotFoundError,
)
from panelapp_link.mcp.untrusted_content import UntrustedText, enforce_untrusted_text_limits
from panelapp_link.models.enums import ENTITY_TYPES, RESPONSE_MODES, ResponseMode
from panelapp_link.observability.metrics import get_metrics
from panelapp_link.services import _live_helpers as helpers
from panelapp_link.services import shaping
from panelapp_link.services.cache import RequestCache

if TYPE_CHECKING:
    from panelapp_link.api.client import PanelAppRestClient
    from panelapp_link.config import PanelAppDataConfigModel

logger = logging.getLogger(__name__)

_MAX_LIMIT = 500

# List/search tools fence several objects per record (panel description + each
# type description; entity phenotypes + evidence) across up to _MAX_LIMIT
# records, so they pass a generous ceiling legitimate data never hits.
_LIST_TOOL_MAX_FENCED_OBJECTS = 10_000

# region argument -> region keys.
_REGION_MAP: dict[str, list[str]] = {
    "both": ["uk", "australia"],
    "uk": ["uk"],
    "australia": ["australia"],
}

_TRUNCATION_HINT = (
    "More results available; re-call with next_offset, or follow next_cursor for paging."
)


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
    """Live business logic over the PanelApp REST APIs (UK + Australia)."""

    def __init__(
        self,
        client: PanelAppRestClient,
        config: PanelAppDataConfigModel | None = None,
        *,
        cache_ttl: int = 21600,
        cache_size: int = 512,
    ) -> None:
        self._client = client
        self._config = config if config is not None else get_data_config()
        self._cache = RequestCache(maxsize=cache_size, ttl=cache_ttl)
        self._cache_ttl = cache_ttl
        self._refresh_interval = self._config.refresh_interval
        self._refresh_task: asyncio.Task[None] | None = None
        self._base_by_region: dict[str, str] = {
            "uk": self._config.uk_api_url,
            "australia": self._config.au_api_url,
        }

    # --- validation helpers --------------------------------------------

    @staticmethod
    def _normalize_region(region: str) -> list[str]:
        """Map a ``region`` argument to region keys.

        ``"both"`` -> ``["uk", "australia"]``; ``"uk"``/``"australia"`` ->
        single-element lists. Anything else raises ``InvalidInputError``.
        """
        keys = _REGION_MAP.get(region)
        if keys is None:
            raise InvalidInputError(
                "Invalid region. Use 'uk', 'australia', or 'both'.",
                field="region",
            )
        return list(keys)

    @staticmethod
    def _validate_mode(mode: str) -> ResponseMode:
        if mode not in RESPONSE_MODES:
            raise InvalidInputError(
                f"Invalid response_mode. Use one of: {', '.join(RESPONSE_MODES)}.",
                field="response_mode",
            )
        return mode

    @staticmethod
    def _validate_entity_type(entity_type: str) -> str:
        if entity_type not in ENTITY_TYPES:
            raise InvalidInputError(
                f"Invalid entity_type. Use one of: {', '.join(ENTITY_TYPES)}.",
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
                f"Invalid min_confidence. Use one of: {', '.join(CONFIDENCE_RANK)}.",
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

    # --- cached live fetches -------------------------------------------

    async def _panel_list(self, region_key: str) -> list[dict[str, Any]]:
        """Return (cached, single-flight) the full panel-summary list for a region."""
        base = self._base_by_region[region_key]
        return await self._cache.get_or_fetch(  # type: ignore[no-any-return]
            f"panels:{region_key}",
            region_key,
            "panels",
            lambda: self._client.list_panels(base),
        )

    async def _signed_off_map(self, region_key: str) -> dict[int, dict[str, Any]]:
        """Return (cached, single-flight) ``{panel_id: {version, signed_off}}`` for a region."""
        base = self._base_by_region[region_key]

        async def fetch() -> dict[int, dict[str, Any]]:
            rows = await self._client.list_signed_off(base)
            out: dict[int, dict[str, Any]] = {}
            for row in rows:
                pid = row.get("id")
                if pid is None:
                    continue
                out[int(pid)] = {"version": row.get("version"), "signed_off": row.get("signed_off")}
            return out

        return await self._cache.get_or_fetch(  # type: ignore[no-any-return]
            f"signedoff:{region_key}", region_key, "signedoff", fetch
        )

    async def _panel_detail(self, region_key: str, panel_id: int) -> dict[str, Any]:
        """Return (cached, single-flight) the full panel detail, mapping 404 -> NotFound."""
        base = self._base_by_region[region_key]

        async def fetch() -> dict[str, Any]:
            try:
                return await self._client.get_panel(base, panel_id)
            except DownloadError as exc:
                if exc.status_code == 404:
                    raise NotFoundError(
                        f"No PanelApp panel {panel_id} in region {region_key!r}. "
                        "Try search_panels to find a panel id."
                    ) from exc
                raise

        return await self._cache.get_or_fetch(  # type: ignore[no-any-return]
            f"panel:{region_key}:{panel_id}", region_key, "panel", fetch
        )

    async def _genes_by_name(self, region_key: str, entity_name: str) -> list[dict[str, Any]]:
        """Return (cached, single-flight) ``/genes/?entity_name=`` results for a region."""
        base = self._base_by_region[region_key]
        return await self._cache.get_or_fetch(  # type: ignore[no-any-return]
            f"genes:{region_key}:{entity_name.upper()}",
            region_key,
            "genes",
            lambda: self._client.get_genes_by_entity_name(base, entity_name),
        )

    # --- search ---------------------------------------------------------

    async def search_panels(
        self,
        query: str = "",
        region: str = "both",
        response_mode: str = "compact",
        limit: int = 20,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search panels by name/disorders/disease group, merged across regions.

        Fetches the (cached) full panel list per region and filters it in memory
        by per-token word-prefix match over name + relevant_disorders +
        disease_group + disease_sub_group (so ``renal`` excludes ``adrenal``; an
        empty query returns all). Results are ranked by relevance (name >
        disorders > disease group, then name/region); an empty query preserves
        alphabetical order. Returns ``{"query","count","total","panels":[...],
        "truncated"?}``. Panels are deduped by ``(region, panel_id)``; paging is
        over the deduped set.
        """
        if cursor is not None:
            offset = _decode_cursor(cursor)
        mode = self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        limit = self._clamp_limit(limit)
        offset = self._validate_offset(offset)
        q = (query or "").strip()
        needle = q.lower()

        seen: set[tuple[str, int]] = set()
        normalized: list[dict[str, Any]] = []
        for region_key in regions:
            panels = await self._panel_list(region_key)
            signed = await self._signed_off_map(region_key)
            for panel in panels:
                pid = panel.get("id")
                if pid is None:
                    continue
                pid_int = int(pid)
                key = (region_key, pid_int)
                if key in seen:
                    continue
                if needle and not helpers.panel_matches(panel, needle):
                    continue
                seen.add(key)
                normalized.append(shaping.normalize_panel(panel, region_key, signed.get(pid_int)))

        normalized = helpers.rank_panels(normalized, needle)
        total = len(normalized)
        page = normalized[offset : offset + limit]
        fenced: list[UntrustedText] = []
        payload: dict[str, Any] = {
            "query": q,
            "count": len(page),
            "total": total,
            "panels": [shaping.shape_panel(r, mode, fenced) for r in page],
        }
        # Panels fence description + each type description; list-tool ceiling.
        enforce_untrusted_text_limits(fenced, max_objects=_LIST_TOOL_MAX_FENCED_OBJECTS)
        trunc = self._truncation(total, limit, offset, len(page))
        if trunc:
            payload["truncated"] = trunc
        return payload

    # --- panel detail ---------------------------------------------------

    async def get_panel(
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
        region_key = self._normalize_region(region)[0]
        detail = await self._panel_detail(region_key, panel_id)
        signed = await self._signed_off_map(region_key)
        row = shaping.normalize_panel(detail, region_key, signed.get(panel_id))
        fenced: list[UntrustedText] = []
        panel = shaping.shape_panel(row, mode, fenced)
        # Single-record tool: default ceiling (128) is the real result cap.
        enforce_untrusted_text_limits(fenced)
        return {"panel": panel}

    async def get_panel_genes(
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
        region_key = self._normalize_region(region)[0]
        entity_type = self._validate_entity_type(entity_type)
        min_rank = self._min_rank(min_confidence)
        limit = self._clamp_limit(limit)
        offset = self._validate_offset(offset)

        detail = await self._panel_detail(region_key, panel_id)
        panel_name = detail.get("name") or ""
        raw_entities = helpers.select_entities(detail, entity_type)
        normalized = [
            shaping.normalize_entity(raw, region_key, panel_id, panel_name) for raw in raw_entities
        ]
        if min_rank is not None:
            normalized = [e for e in normalized if (e.get("confidence_rank") or 0) >= min_rank]

        total = len(normalized)
        page = normalized[offset : offset + limit]
        fenced: list[UntrustedText] = []
        payload: dict[str, Any] = {
            "panel_id": panel_id,
            "region": region_key,
            "entity_type": entity_type,
            "count": len(page),
            "total": total,
            "entities": [shaping.shape_entity(e, mode, fenced) for e in page],
        }
        # Real ceiling: up to _MAX_LIMIT entities/page, each with 2 prose lists.
        enforce_untrusted_text_limits(fenced, max_objects=_LIST_TOOL_MAX_FENCED_OBJECTS)
        trunc = self._truncation(total, limit, offset, len(page))
        if trunc:
            payload["truncated"] = trunc
        return payload

    # --- gene -> panels -------------------------------------------------

    async def get_gene_panels(
        self,
        gene_symbol: str | None = None,
        hgnc_id: str | None = None,
        region: str = "both",
        min_confidence: str | None = None,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        """Return the panels a gene appears on, across regions, sorted by confidence.

        PanelApp is queried by ``entity_name`` (gene symbol). A bare ``hgnc_id``
        cannot drive the query, so ``gene_symbol`` is required; ``hgnc_id`` (when
        supplied alongside) filters the hits. Returns ``{"gene","count","panels"}``
        where ``panels`` are shaped gene->panel hit rows ordered by confidence
        rank (desc) then region. Raises ``NotFoundError`` when the gene is absent.
        """
        self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        min_rank = self._min_rank(min_confidence)
        symbol = (gene_symbol or "").strip()
        if not symbol:
            raise InvalidInputError(
                "Provide gene_symbol. PanelApp is queried by gene symbol; an "
                "hgnc_id alone cannot drive the query.",
                field="gene_symbol",
            )
        hid = (hgnc_id or "").strip() or None

        results = await self._gather_gene_results(regions, symbol)
        if not results:
            raise NotFoundError(
                f"No PanelApp gene found for {symbol!r}. Try resolve_gene to confirm a symbol."
            )

        hits: list[dict[str, Any]] = []
        for region_key, result in results:
            gene_data = result.get("gene_data") or {}
            if hid is not None and gene_data.get("hgnc_id") != hid:
                continue
            panel = result.get("panel") or {}
            level, label, rank = helpers.confidence(result.get("confidence_level"))
            if min_rank is not None and (rank or 0) < min_rank:
                continue
            hits.append(
                {
                    "region": region_key,
                    "panel_id": int(panel["id"]) if panel.get("id") is not None else None,
                    "panel_name": panel.get("name"),
                    "version": helpers.as_str(panel.get("version")),
                    "confidence_label": label,
                    "confidence_level": level,
                    "confidence_rank": rank,
                    "mode_of_inheritance": result.get("mode_of_inheritance"),
                }
            )

        hits.sort(key=lambda h: (-(h.get("confidence_rank") or 0), h.get("region") or ""))
        return {
            "gene": helpers.gene_identity(symbol, results, hits),
            "count": len(hits),
            "panels": [shaping.shape_gene_panel_hit(h) for h in hits],
        }

    async def resolve_gene(
        self,
        query: str | None = None,
        gene_symbol: str | None = None,
        region: str = "both",
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        """Resolve a symbol / free-text query to a single rolled-up gene.

        Returns ``{"query","gene","matches":[...]}``. PanelApp resolves by gene
        symbol; ``query`` and ``gene_symbol`` are accepted (``query`` wins when
        ``gene_symbol`` is empty). ``region`` (uk|australia|both) scopes the
        lookup. Raises ``NotFoundError`` when nothing matches.
        """
        self._validate_mode(response_mode)
        symbol = (gene_symbol or "").strip() or (query or "").strip()
        if not symbol:
            raise InvalidInputError(
                "Provide a gene_symbol or a non-empty query (PanelApp resolves by gene symbol).",
                field="gene_symbol",
            )
        regions = self._normalize_region(region)
        results = await self._gather_gene_results(regions, symbol)
        if not results:
            raise NotFoundError(
                f"Could not resolve {symbol!r} to a PanelApp gene. "
                "Try search_panels to discover panels first."
            )
        gene = helpers.gene_identity(symbol, results, helpers.results_to_hits(results))
        return {
            "query": symbol.upper(),
            "gene": gene,
            "matches": [gene],
        }

    async def _gather_gene_results(
        self, regions: list[str], symbol: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Fetch ``/genes/?entity_name=`` for each region and tag results by region."""
        per_region = await asyncio.gather(
            *(self._genes_by_name(region_key, symbol) for region_key in regions)
        )
        out: list[tuple[str, dict[str, Any]]] = []
        for region_key, rows in zip(regions, per_region, strict=True):
            for row in rows:
                out.append((region_key, row))
        return out

    # --- warm-up / background refresh -----------------------------------

    async def prewarm(self) -> None:
        """Pre-fetch the heavy list endpoints for both regions (best-effort).

        Populates the panel-summary + signed-off lists so the first
        ``search_panels`` never pays the cold double-fetch. Errors are swallowed
        (logged) so a transient upstream failure never blocks startup.
        """
        try:
            await asyncio.gather(
                *(self._panel_list(r) for r in _REGION_MAP["both"]),
                *(self._signed_off_map(r) for r in _REGION_MAP["both"]),
            )
        except Exception as exc:  # best-effort warm-up; never fatal
            # Log only the exception TYPE -- never str(exc), which can carry
            # request/upstream detail (M3 no-raw-detail-in-logs invariant).
            logger.warning("panelapp prewarm failed: %s", exc.__class__.__name__)

    async def refresh_panel_lists(self) -> None:
        """Force-refresh the panel + signed-off lists for both regions.

        Overwrites the cached lists (stale-while-revalidate) so a long-lived
        process keeps ``search_panels`` warm past the TTL without an on-path miss.
        """
        for region_key in _REGION_MAP["both"]:
            base = self._base_by_region[region_key]
            rows = await self._client.list_panels(base)
            self._cache.put(f"panels:{region_key}", rows)
            signed = await self._client.list_signed_off(base)
            out: dict[int, dict[str, Any]] = {}
            for row in signed:
                pid = row.get("id")
                if pid is None:
                    continue
                out[int(pid)] = {"version": row.get("version"), "signed_off": row.get("signed_off")}
            self._cache.put(f"signedoff:{region_key}", out)

    async def start_background_refresh(self) -> asyncio.Task[None] | None:
        """Start the periodic list-refresh loop if ``refresh_interval`` > 0."""
        if self._refresh_interval <= 0:
            return None
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.ensure_future(self._refresh_loop(self._refresh_interval))
        return self._refresh_task

    async def _refresh_loop(self, interval: int) -> None:
        """Sleep ``interval`` seconds, then refresh the lists; repeat until cancelled."""
        while True:  # pragma: no cover - timing loop exercised via refresh_panel_lists
            await asyncio.sleep(interval)
            try:
                await self.refresh_panel_lists()
            except Exception as exc:
                # Log only the exception TYPE (M3 no-raw-detail-in-logs invariant).
                logger.warning("panelapp background refresh failed: %s", exc.__class__.__name__)

    async def aclose(self) -> None:
        """Cancel the background refresh task, if any."""
        task = self._refresh_task
        self._refresh_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- discovery ------------------------------------------------------

    def capabilities_data(self) -> dict[str, Any]:
        """Return the live data block for capabilities (never raises)."""
        return {
            "mode": "live",
            "sources": {
                "uk": self._config.uk_api_url,
                "australia": self._config.au_api_url,
            },
            "cache_ttl_seconds": self._cache_ttl,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return live source/config + cache stats + the RED metrics snapshot."""
        return {
            "mode": "live",
            "sources": {
                "uk": self._config.uk_api_url,
                "australia": self._config.au_api_url,
            },
            "cache_ttl_seconds": self._cache_ttl,
            "cache": self._cache.stats(),
            "metrics": get_metrics().snapshot(),
        }
