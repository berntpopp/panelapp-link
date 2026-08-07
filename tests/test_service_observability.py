"""Service-level observability + speed tests over the respx-mocked fixtures.

Proves the warm-repeat path the review flagged: a second identical query is fully
served from cache (no upstream), prewarm/refresh warm the heavy list endpoints,
and diagnostics expose the RED metrics snapshot.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DownloadError
from panelapp_link.observability import telemetry as tel
from panelapp_link.observability.metrics import reset_metrics
from panelapp_link.services.panelapp_service import PanelAppService


class GenerationClient:
    """Small generation-aware client for publication race/failure tests."""

    def __init__(self) -> None:
        self.generation = "old"
        self.fail_leg: str | None = None

    async def list_panels(self, base: str) -> list[dict[str, Any]]:
        region = "uk" if "uk.test" in base else "australia"
        self._maybe_fail(f"{region}_panels")
        panel_id = 1 if region == "uk" else 2
        return [{"id": panel_id, "name": f"{self.generation}-{region}", "version": "1"}]

    async def list_signed_off(self, base: str) -> list[dict[str, Any]]:
        region = "uk" if "uk.test" in base else "australia"
        self._maybe_fail(f"{region}_signed_off")
        panel_id = 1 if region == "uk" else 2
        return [
            {
                "id": panel_id,
                "version": self.generation,
                "signed_off": f"{self.generation}-date",
            }
        ]

    def _maybe_fail(self, leg: str) -> None:
        if self.fail_leg == leg:
            raise DownloadError("safe test failure")


class PausedFailureClient(GenerationClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.uk_panel_calls = 0

    async def list_panels(self, base: str) -> list[dict[str, Any]]:
        if "uk.test" in base:
            self.uk_panel_calls += 1
            self.started.set()
            await self.release.wait()
            raise DownloadError("safe test failure")
        return await super().list_panels(base)


class CancelledRetryClient(GenerationClient):
    def __init__(self) -> None:
        super().__init__()
        self.uk_panel_calls = 0
        self.retry_started = asyncio.Event()

    async def list_panels(self, base: str) -> list[dict[str, Any]]:
        if "uk.test" not in base:
            return await super().list_panels(base)
        self.uk_panel_calls += 1
        if self.uk_panel_calls == 1:
            raise DownloadError("safe first failure")
        if self.uk_panel_calls == 2:
            self.retry_started.set()
            await asyncio.Event().wait()
        return await super().list_panels(base)


class CancelledReplacementClient(GenerationClient):
    def __init__(self) -> None:
        super().__init__()
        self.uk_panel_calls = 0
        self.replacement_started = asyncio.Event()

    async def list_panels(self, base: str) -> list[dict[str, Any]]:
        if "uk.test" not in base:
            return await super().list_panels(base)
        self.uk_panel_calls += 1
        if self.uk_panel_calls == 2:
            self.replacement_started.set()
            await asyncio.Event().wait()
        return await super().list_panels(base)


def _generation_service(client: GenerationClient) -> PanelAppService:
    config = PanelAppDataConfigModel(
        uk_api_url="https://uk.test/api/v1",
        au_api_url="https://australia.test/api/v1",
        refresh_interval=60,
        cache_ttl=3600,
    )
    return PanelAppService(client, config, cache_ttl=3600)  # type: ignore[arg-type]


def _generation_pairs(payload: dict[str, Any]) -> frozenset[tuple[str, str | None]]:
    return frozenset(
        (panel["name"].split("-", 1)[0], panel["signed_off_version"]) for panel in payload["panels"]
    )


async def test_cold_search_then_warm_repeat_hits_cache(live_service: PanelAppService) -> None:
    with tel.request_scope("cold") as cold:
        await live_service.search_panels(query="", region="both", limit=100)
    # Cold: list + signed-off fetched per region (the expensive double-fetch).
    assert len(cold.upstream) >= 2
    assert cold.cache_misses == 1

    with tel.request_scope("warm") as warm:
        await live_service.search_panels(query="", region="both", limit=100)
    # Warm repeat: fully served from cache, zero upstream calls.
    assert warm.upstream == []
    assert warm.cache_hits == 1
    assert warm.cache_misses == 0


async def test_prewarm_warms_list_endpoints(live_service: PanelAppService) -> None:
    await live_service.prewarm()
    with tel.request_scope("p") as scope:
        await live_service.search_panels(query="", region="both", limit=100)
    assert scope.upstream == []
    assert scope.cache_hits == 1


async def test_refresh_panel_lists_warms_cache(live_service: PanelAppService) -> None:
    await live_service.refresh_panel_lists()
    with tel.request_scope("r") as scope:
        await live_service.search_panels(query="", region="both", limit=100)
    assert scope.upstream == []


async def test_diagnostics_includes_metrics_snapshot(live_service: PanelAppService) -> None:
    reset_metrics()
    with tel.request_scope("d"):
        await live_service.search_panels(query="porphyria", region="uk")
    diag = live_service.diagnostics()
    assert "metrics" in diag
    metrics = diag["metrics"]
    assert "cache" in metrics
    assert "tool_duration_ms" in metrics
    assert metrics["cache"]["miss"] >= 1


async def test_background_refresh_disabled_returns_none(live_service: PanelAppService) -> None:
    # The fixture config leaves refresh_interval at its default (0 = disabled).
    assert await live_service.start_background_refresh() is None
    await live_service.aclose()  # safe no-op when no task is running


async def test_background_refresh_enabled_starts_and_cancels(
    live_service: PanelAppService,
) -> None:
    live_service._refresh_interval = 3600  # enable without waiting a real cycle
    task = await live_service.start_background_refresh()
    assert isinstance(task, asyncio.Task)
    assert not task.done()
    await live_service.aclose()
    assert task.cancelled() or task.done()


async def test_generation_publication_exposes_only_complete_old_or_new_results() -> None:
    client = GenerationClient()
    service = _generation_service(client)
    old_payload = await service.search_panels(region="both", limit=10)

    fetched = asyncio.Event()
    publish = asyncio.Event()
    original_fetch = service._fetch_generation

    async def paused_fetch():
        generation = await original_fetch()
        fetched.set()
        await publish.wait()
        return generation

    service._fetch_generation = paused_fetch  # type: ignore[method-assign]
    client.generation = "new"
    refresh_task = asyncio.create_task(service.refresh_panel_lists())
    await fetched.wait()
    during = await asyncio.gather(
        *(service.search_panels(region="both", limit=10) for _ in range(10))
    )
    publish.set()
    await refresh_task
    after = await asyncio.gather(
        *(service.search_panels(region="both", limit=10) for _ in range(10))
    )

    old = _generation_pairs(old_payload)
    complete_old = frozenset({("old", "old")})
    complete_new = frozenset({("new", "new")})
    assert old == complete_old
    assert {_generation_pairs(result) for result in during + after} <= {
        complete_old,
        complete_new,
    }
    assert all(_generation_pairs(result) == complete_old for result in during)
    assert all(_generation_pairs(result) == complete_new for result in after)


@pytest.mark.parametrize(
    "fail_leg",
    ["uk_panels", "uk_signed_off", "australia_panels", "australia_signed_off"],
)
async def test_failed_generation_acquisition_preserves_identical_old_reference(
    fail_leg: str,
) -> None:
    client = GenerationClient()
    service = _generation_service(client)
    await service.search_panels(region="both", limit=10)
    old_generation = service._generation
    client.generation = "new"
    client.fail_leg = fail_leg

    with pytest.raises(DownloadError):
        await service.refresh_panel_lists()

    assert service._generation is old_generation
    payload = await service.search_panels(region="both", limit=10)
    assert _generation_pairs(payload) == frozenset({("old", "old")})


async def test_concurrent_failed_cold_readers_share_one_acquisition() -> None:
    client = PausedFailureClient()
    service = _generation_service(client)
    readers = [asyncio.create_task(service.search_panels(region="both")) for _ in range(10)]
    await client.started.wait()
    while len(service._generation_lock._waiters or ()) < len(readers) - 1:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    client.release.set()

    results = await asyncio.gather(*readers, return_exceptions=True)

    assert all(isinstance(result, DownloadError) for result in results)
    assert client.uk_panel_calls == 1
    assert service.refresh_snapshot()["failures_total"] == 1


async def test_cancelled_retry_does_not_replay_prior_failure_to_waiter() -> None:
    client = CancelledRetryClient()
    service = _generation_service(client)
    with pytest.raises(DownloadError):
        await service.search_panels(region="both")

    retry_owner = asyncio.create_task(service.search_panels(region="both"))
    await client.retry_started.wait()
    waiter = asyncio.create_task(service.search_panels(region="both"))
    while len(service._generation_lock._waiters or ()) < 1:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    retry_owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retry_owner

    result = await waiter
    assert result["total"] == 2
    assert client.uk_panel_calls == 3
    assert service.refresh_snapshot()["failures_total"] == 1


async def test_cancelled_replacement_waiter_does_not_return_expired_generation() -> None:
    client = CancelledReplacementClient()
    service = _generation_service(client)
    await service.search_panels(region="both")
    old_generation = service._generation
    assert old_generation is not None
    service._generation = replace(old_generation, expires_monotonic=0.0)

    replacement_owner = asyncio.create_task(service.search_panels(region="both"))
    await client.replacement_started.wait()
    waiter = asyncio.create_task(service.search_panels(region="both"))
    while len(service._generation_lock._waiters or ()) < 1:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    replacement_owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replacement_owner

    await waiter
    assert client.uk_panel_calls == 3
    assert service._generation is not old_generation
