"""Per-request telemetry scope for PanelApp-Link.

A small ``ContextVar``-scoped accumulator the cache layer writes to (cache
hit/miss/coalesced and per-region upstream timings) and the MCP envelope reads to
fold a compact, mode-agnostic ``cache``/``upstream`` block into ``_meta``. This is
the *per-call* breadcrumb that lets an agent (and an operator) see why a single
call took N seconds; process-wide aggregates live in
:mod:`panelapp_link.observability.metrics`.

Every ``record_*`` helper is a no-op when there is no active scope, so the service
can be exercised directly in unit tests (and the helpers are cheap on hot paths)
without any envelope establishing a scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestTelemetry:
    """Mutable per-request accumulator (one object per MCP tool call)."""

    request_id: str
    cache_hits: int = 0
    cache_misses: int = 0
    coalesced: int = 0
    upstream: list[dict[str, Any]] = field(default_factory=list)


_current: ContextVar[RequestTelemetry | None] = ContextVar(
    "panelapp_request_telemetry", default=None
)


def current() -> RequestTelemetry | None:
    """Return the active telemetry accumulator, or ``None`` outside a scope."""
    return _current.get()


def current_request_id() -> str | None:
    """Return the active request id, or ``None`` outside a scope."""
    scope = _current.get()
    return scope.request_id if scope is not None else None


@contextmanager
def request_scope(request_id: str) -> Iterator[RequestTelemetry]:
    """Bind a fresh telemetry accumulator for the duration of one tool call.

    The ``ContextVar`` is reset on exit so scopes never leak across calls. A child
    task spawned for single-flight inherits the *same* accumulator object (the
    context is copied by reference at task creation), so its cache/upstream writes
    flow back to this scope.
    """
    scope = RequestTelemetry(request_id=request_id)
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def record_cache_hit() -> None:
    """Count a cache hit on the active scope (no-op outside a scope)."""
    scope = _current.get()
    if scope is not None:
        scope.cache_hits += 1


def record_cache_miss() -> None:
    """Count a cache miss (an upstream fill) on the active scope."""
    scope = _current.get()
    if scope is not None:
        scope.cache_misses += 1


def record_coalesced() -> None:
    """Count a single-flight coalesce (this call shared an in-flight fetch)."""
    scope = _current.get()
    if scope is not None:
        scope.coalesced += 1


def record_upstream(region: str, endpoint: str, ms: float) -> None:
    """Record one upstream fetch's region, endpoint, and wall-clock duration."""
    scope = _current.get()
    if scope is not None:
        scope.upstream.append({"region": region, "endpoint": endpoint, "ms": round(ms, 2)})


def _cache_label(scope: RequestTelemetry) -> str | None:
    """Collapse hit/miss/coalesced counters into a single ``_meta.cache`` label."""
    if scope.cache_hits and not scope.cache_misses and not scope.coalesced:
        return "hit"
    if scope.coalesced and not scope.cache_misses and not scope.cache_hits:
        return "coalesced"
    if scope.cache_misses and not scope.cache_hits and not scope.coalesced:
        return "miss"
    if scope.cache_hits or scope.cache_misses or scope.coalesced:
        return "partial"
    return None


def telemetry_meta(scope: RequestTelemetry) -> dict[str, Any]:
    """Build the compact ``_meta`` telemetry block for one call.

    Emits ``cache`` (hit|miss|coalesced|partial), ``upstream_ms`` (total upstream
    wall-clock), and ``upstream`` (per-region ``{calls, ms}``). All keys are
    omitted when there was no cache/upstream activity (e.g. capabilities).
    """
    meta: dict[str, Any] = {}
    label = _cache_label(scope)
    if label is not None:
        meta["cache"] = label
    if scope.upstream:
        per_region: dict[str, dict[str, Any]] = {}
        total_ms = 0.0
        for call in scope.upstream:
            bucket = per_region.setdefault(call["region"], {"calls": 0, "ms": 0.0})
            bucket["calls"] += 1
            bucket["ms"] = round(bucket["ms"] + call["ms"], 2)
            total_ms += call["ms"]
        meta["upstream_ms"] = round(total_ms, 2)
        meta["upstream"] = per_region
    return meta
