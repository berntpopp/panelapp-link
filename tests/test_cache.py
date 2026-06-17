"""Tests for the request cache + single-flight coalescing (services.cache).

Single-flight is the core speed fix: concurrent identical upstream fetches share
one in-flight call instead of stampeding the rate-limited PanelApp API. The cache
absorbs warm repeats; telemetry/metrics record hit/miss/coalesced so the effect
is observable.
"""

from __future__ import annotations

import asyncio

import pytest

from panelapp_link.observability import telemetry as tel
from panelapp_link.observability.metrics import get_metrics, reset_metrics
from panelapp_link.services.cache import RequestCache, TTLCache


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_metrics()


def test_ttlcache_disabled_when_maxsize_zero() -> None:
    cache = TTLCache(0, 60)
    cache.put("k", "v")
    assert cache.get("k") is None
    assert cache.stats()["maxsize"] == 0


def test_ttlcache_evicts_oldest_when_full() -> None:
    cache = TTLCache(2, 60)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)  # evicts "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


async def test_first_call_fetches_and_caches() -> None:
    cache = RequestCache(maxsize=16, ttl=60)
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        return "VALUE"

    with tel.request_scope("r") as scope:
        first = await cache.get_or_fetch("k", "uk", "panels", fetch)
        second = await cache.get_or_fetch("k", "uk", "panels", fetch)

    assert first == second == "VALUE"
    assert calls == 1  # second served from cache
    assert scope.cache_misses == 1
    assert scope.cache_hits == 1
    assert get_metrics().snapshot()["cache"]["hit"] == 1


async def test_concurrent_identical_calls_coalesce() -> None:
    cache = RequestCache(maxsize=16, ttl=60)
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "VALUE"

    with tel.request_scope("r") as scope:

        async def caller() -> str:
            return await cache.get_or_fetch("k", "uk", "panels", fetch)

        tasks = [asyncio.create_task(caller()) for _ in range(5)]
        await started.wait()
        await asyncio.sleep(0)  # let the other 4 reach the coalesce point
        release.set()
        results = await asyncio.gather(*tasks)

    assert results == ["VALUE"] * 5
    assert calls == 1  # single-flight: one upstream call for five concurrent lookups
    assert scope.cache_misses == 1
    assert scope.coalesced == 4
    assert get_metrics().snapshot()["cache"]["coalesced"] == 4


async def test_failed_fetch_is_not_cached_and_clears_inflight() -> None:
    cache = RequestCache(maxsize=16, ttl=60)
    attempts = 0

    async def fetch() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "OK"

    with tel.request_scope("r"):
        with pytest.raises(RuntimeError):
            await cache.get_or_fetch("k", "uk", "panels", fetch)
        # in-flight entry cleared, nothing cached -> a retry actually re-fetches
        result = await cache.get_or_fetch("k", "uk", "panels", fetch)

    assert result == "OK"
    assert attempts == 2


async def test_upstream_timing_recorded() -> None:
    cache = RequestCache(maxsize=16, ttl=60)

    async def fetch() -> str:
        await asyncio.sleep(0.01)
        return "VALUE"

    with tel.request_scope("r") as scope:
        await cache.get_or_fetch("k", "australia", "genes", fetch)

    assert len(scope.upstream) == 1
    assert scope.upstream[0]["region"] == "australia"
    assert scope.upstream[0]["endpoint"] == "genes"
    assert scope.upstream[0]["ms"] >= 0
    assert "australia" in get_metrics().snapshot()["upstream_duration_ms"]
