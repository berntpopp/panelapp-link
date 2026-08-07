"""Live PanelApp business logic with atomic panel-list generations.

Public methods return JSON-ready payloads; the MCP layer adds envelopes and
metadata. Detail and gene requests retain the bounded in-memory request cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from panelapp_link.config import get_data_config
from panelapp_link.exceptions import (
    DownloadError,
    InvalidInputError,
    NotFoundError,
)
from panelapp_link.mcp.untrusted_content import UntrustedText, enforce_untrusted_text_limits
from panelapp_link.models.enums import ENTITY_TYPES, RESPONSE_MODES, ResponseMode
from panelapp_link.observability import telemetry, tracing
from panelapp_link.observability.metrics import get_metrics
from panelapp_link.services import _live_helpers as helpers
from panelapp_link.services import shaping
from panelapp_link.services.cache import RequestCache
from panelapp_link.services.refresh_state import PanelListGeneration, RefreshState

if TYPE_CHECKING:
    from panelapp_link.api.client import PanelAppRestClient
    from panelapp_link.config import PanelAppDataConfigModel

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
        self._generation: PanelListGeneration | None = None
        self._generation_lock = asyncio.Lock()
        self._generation_error: Exception | None = None
        self._refresh_state = RefreshState(
            interval_seconds=self._refresh_interval,
            cache_ttl_seconds=cache_ttl,
        )
        self._base_by_region: dict[str, str] = {
            "uk": self._config.uk_api_url,
            "australia": self._config.au_api_url,
        }

    # --- validation helpers --------------------------------------------

    @staticmethod
    def _normalize_region(region: str) -> list[str]:
        """Map the public region argument to concrete region keys."""
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
            "next_cursor": helpers.encode_cursor(next_offset),
            "hint": _TRUNCATION_HINT,
        }

    # --- atomic panel-list generation ----------------------------------

    async def _fetch_generation_leg(
        self, region: str, endpoint: str, fetch: Callable[[], Awaitable[Any]]
    ) -> Any:
        start = time.perf_counter()
        with tracing.upstream_span(region, endpoint, telemetry.current_request_id()):
            value = await fetch()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        telemetry.record_upstream(region, endpoint, elapsed_ms)
        get_metrics().record_upstream(region, elapsed_ms)
        return value

    async def _fetch_generation(self) -> PanelListGeneration:
        """Fetch all four list legs into one unpublished generation."""
        uk = self._base_by_region["uk"]
        au = self._base_by_region["australia"]
        uk_panels, uk_signed, au_panels, au_signed = await asyncio.gather(
            self._fetch_generation_leg("uk", "panels", lambda: self._client.list_panels(uk)),
            self._fetch_generation_leg("uk", "signedoff", lambda: self._client.list_signed_off(uk)),
            self._fetch_generation_leg("australia", "panels", lambda: self._client.list_panels(au)),
            self._fetch_generation_leg(
                "australia", "signedoff", lambda: self._client.list_signed_off(au)
            ),
        )

        def signed_map(rows: list[dict[str, Any]]) -> MappingProxyType[int, dict[str, Any]]:
            return MappingProxyType(
                {
                    int(row["id"]): {
                        "version": row.get("version"),
                        "signed_off": row.get("signed_off"),
                    }
                    for row in rows
                    if row.get("id") is not None
                }
            )

        created_monotonic = time.monotonic()
        return PanelListGeneration(
            panels=MappingProxyType({"uk": tuple(uk_panels), "australia": tuple(au_panels)}),
            signed_off=MappingProxyType(
                {"uk": signed_map(uk_signed), "australia": signed_map(au_signed)}
            ),
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            created_monotonic=created_monotonic,
            expires_monotonic=created_monotonic + self._cache_ttl,
        )

    async def _ensure_generation(self, *, force: bool = False) -> PanelListGeneration:
        """Return a current generation, single-flighting acquisition/publication."""
        observed = self._generation
        if not force and observed is not None and time.monotonic() < observed.expires_monotonic:
            telemetry.record_cache_hit()
            get_metrics().record_cache("hit")
            return observed
        waited = self._generation_lock.locked()
        if waited:
            telemetry.record_coalesced()
            get_metrics().record_cache("coalesced")
        async with self._generation_lock:
            current = self._generation
            if current is not None and (
                (not force and time.monotonic() < current.expires_monotonic)
                or (force and current is not observed)
            ):
                return current
            if waited and self._generation_error is not None:
                raise self._generation_error
            self._generation_error = None
            self._refresh_state.record_attempt()
            try:
                generation = await self._fetch_generation()
            except Exception as exc:
                self._generation_error = exc
                self._refresh_state.record_failure(exc)
                raise
            self._generation = generation
            self._generation_error = None
            self._refresh_state.record_success()
            telemetry.record_cache_miss()
            get_metrics().record_cache("miss")
            return generation

    # --- cached detail/gene fetches ------------------------------------

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
        """Search one captured panel-list generation across selected regions."""
        if cursor is not None:
            offset = helpers.decode_cursor(cursor)
        mode = self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        limit = self._clamp_limit(limit)
        offset = self._validate_offset(offset)
        q = (query or "").strip()
        needle = q.lower()

        generation = await self._ensure_generation()
        seen: set[tuple[str, int]] = set()
        normalized: list[dict[str, Any]] = []
        for region_key in regions:
            panels = generation.panels[region_key]
            signed = generation.signed_off[region_key]
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
        # A partial page must declare has_more (Response-Envelope pagination honesty).
        payload["has_more"] = trunc is not None
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
        """Return one panel detail, annotated from one captured generation."""
        mode = self._validate_mode(response_mode)
        if region == "both":
            raise InvalidInputError(
                "region must be 'uk' or 'australia' for get_panel (panel ids are "
                "per-region; 'both' is not allowed).",
                field="region",
            )
        helpers.validate_panel_id(panel_id)
        region_key = self._normalize_region(region)[0]
        generation = await self._ensure_generation()
        detail = await self._panel_detail(region_key, panel_id)
        signed = generation.signed_off[region_key]
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
        """Return a panel's entities, filtered by type and confidence."""
        if cursor is not None:
            offset = helpers.decode_cursor(cursor)
        mode = self._validate_mode(response_mode)
        if region == "both":
            raise InvalidInputError(
                "region must be 'uk' or 'australia' for get_panel_genes.",
                field="region",
            )
        helpers.validate_panel_id(panel_id)
        region_key = self._normalize_region(region)[0]
        entity_type = self._validate_entity_type(entity_type)
        min_rank = helpers.min_rank(min_confidence)
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
        payload["has_more"] = trunc is not None
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
        """Return a gene's panels across regions, sorted by confidence."""
        self._validate_mode(response_mode)
        regions = self._normalize_region(region)
        min_rank = helpers.min_rank(min_confidence)
        symbol = (gene_symbol or "").strip()
        if not symbol:
            raise InvalidInputError(
                "Provide gene_symbol. PanelApp is queried by gene symbol; an "
                "hgnc_id alone cannot drive the query.",
                field="gene_symbol",
            )
        helpers.reject_hgnc_curie(symbol, field="gene_symbol")
        hid = (hgnc_id or "").strip() or None
        if hid is not None:
            if not helpers.is_hgnc_curie(hid):
                raise InvalidInputError(
                    "hgnc_id must be an HGNC CURIE like HGNC:1100.", field="hgnc_id"
                )
            # PanelApp stores an uppercase HGNC prefix.
            hid = hid.upper()

        results = await self._gather_gene_results(regions, symbol)
        if not results:
            raise NotFoundError(
                f"No PanelApp gene found for {symbol!r}. Try resolve_gene to confirm a symbol."
            )
        # Fail loudly on an HGNC mismatch; confidence filtering may still be empty.
        if hid is not None and not any(
            (result.get("gene_data") or {}).get("hgnc_id") == hid for _rk, result in results
        ):
            raise NotFoundError(
                f"Gene {symbol!r} does not carry hgnc_id {hid} on any PanelApp entity. "
                "Drop hgnc_id or pass the gene's actual HGNC id."
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
        # Name the parameter the caller actually supplied so field_errors never points
        # at a param the calling tool does not expose (the resolve_gene tool takes only
        # `query`; the service also accepts `gene_symbol`).
        lookup_field = "gene_symbol" if (gene_symbol or "").strip() else "query"
        symbol = (gene_symbol or "").strip() or (query or "").strip()
        if not symbol:
            raise InvalidInputError(
                "Provide a gene_symbol or a non-empty query (PanelApp resolves by gene symbol).",
                field=lookup_field,
            )
        helpers.reject_hgnc_curie(symbol, field=lookup_field)
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
        """Best-effort acquisition of one complete generation for startup."""
        with contextlib.suppress(Exception):  # refresh state logs the safe error type
            await self._ensure_generation()

    async def refresh_panel_lists(self) -> None:
        """Force-fetch and atomically publish one complete generation."""
        await self._ensure_generation(force=True)

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
            with contextlib.suppress(Exception):  # state already logged the safe error type
                await self.refresh_panel_lists()

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

    def refresh_snapshot(self) -> dict[str, object]:
        """Return the caller-visible refresh snapshot."""
        snapshot = self._refresh_state.snapshot()
        get_metrics().record_refresh(
            failures_total=snapshot.failures_total,
            consecutive_failures=snapshot.consecutive_failures,
            age_seconds=snapshot.age_seconds,
            status=snapshot.status,
        )
        return snapshot.to_dict()

    def diagnostics(self) -> dict[str, Any]:
        """Return live source/config + cache stats + the RED metrics snapshot."""
        refresh = self.refresh_snapshot()
        return {
            "mode": "live",
            "sources": {
                "uk": self._config.uk_api_url,
                "australia": self._config.au_api_url,
            },
            "cache_ttl_seconds": self._cache_ttl,
            "cache": self._cache.stats(),
            "metrics": get_metrics().snapshot(),
            "refresh": refresh,
        }
