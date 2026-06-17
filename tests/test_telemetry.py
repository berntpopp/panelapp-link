"""Tests for per-request telemetry (panelapp_link.observability.telemetry).

The telemetry is a ContextVar-scoped accumulator that the cache layer writes to
(cache hit/miss/coalesced + per-region upstream timings) and the MCP envelope
reads to fold a compact ``cache``/``upstream`` block into ``_meta``.
"""

from __future__ import annotations

from panelapp_link.observability import telemetry as tel


def test_record_helpers_noop_without_scope() -> None:
    # Outside a request scope, every record_* is a safe no-op (service unit tests
    # call the service directly, with no envelope establishing a scope).
    assert tel.current() is None
    tel.record_cache_hit()
    tel.record_cache_miss()
    tel.record_coalesced()
    tel.record_upstream("uk", "panels", 12.5)
    assert tel.current() is None


def test_scope_sets_and_resets() -> None:
    assert tel.current() is None
    with tel.request_scope("abc123def456") as scope:
        assert tel.current() is scope
        assert scope.request_id == "abc123def456"
        assert tel.current_request_id() == "abc123def456"
    assert tel.current() is None
    assert tel.current_request_id() is None


def test_meta_cache_hit_label() -> None:
    with tel.request_scope("r") as scope:
        tel.record_cache_hit()
        tel.record_cache_hit()
        meta = tel.telemetry_meta(scope)
    assert meta["cache"] == "hit"
    assert "upstream" not in meta


def test_meta_cache_miss_label_and_upstream_timing() -> None:
    with tel.request_scope("r") as scope:
        tel.record_cache_miss()
        tel.record_upstream("uk", "panels", 100.0)
        tel.record_upstream("australia", "panels", 50.0)
        tel.record_upstream("uk", "signedoff", 25.0)
        meta = tel.telemetry_meta(scope)
    assert meta["cache"] == "miss"
    assert meta["upstream_ms"] == 175.0
    assert meta["upstream"]["uk"] == {"calls": 2, "ms": 125.0}
    assert meta["upstream"]["australia"] == {"calls": 1, "ms": 50.0}


def test_meta_coalesced_label() -> None:
    with tel.request_scope("r") as scope:
        tel.record_coalesced()
        meta = tel.telemetry_meta(scope)
    assert meta["cache"] == "coalesced"


def test_meta_partial_label_when_hit_and_miss_mix() -> None:
    with tel.request_scope("r") as scope:
        tel.record_cache_hit()
        tel.record_cache_miss()
        meta = tel.telemetry_meta(scope)
    assert meta["cache"] == "partial"


def test_meta_empty_when_no_cache_activity() -> None:
    with tel.request_scope("r") as scope:
        meta = tel.telemetry_meta(scope)
    assert "cache" not in meta
    assert "upstream" not in meta
