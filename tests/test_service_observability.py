"""Service-level observability + speed tests over the respx-mocked fixtures.

Proves the warm-repeat path the review flagged: a second identical query is fully
served from cache (no upstream), prewarm/refresh warm the heavy list endpoints,
and diagnostics expose the RED metrics snapshot.
"""

from __future__ import annotations

import asyncio

from panelapp_link.observability import telemetry as tel
from panelapp_link.observability.metrics import reset_metrics
from panelapp_link.services.panelapp_service import PanelAppService


async def test_cold_search_then_warm_repeat_hits_cache(live_service: PanelAppService) -> None:
    with tel.request_scope("cold") as cold:
        await live_service.search_panels(query="", region="both", limit=100)
    # Cold: list + signed-off fetched per region (the expensive double-fetch).
    assert len(cold.upstream) >= 2
    assert cold.cache_misses >= 2

    with tel.request_scope("warm") as warm:
        await live_service.search_panels(query="", region="both", limit=100)
    # Warm repeat: fully served from cache, zero upstream calls.
    assert warm.upstream == []
    assert warm.cache_hits >= 2
    assert warm.cache_misses == 0


async def test_prewarm_warms_list_endpoints(live_service: PanelAppService) -> None:
    await live_service.prewarm()
    with tel.request_scope("p") as scope:
        await live_service.search_panels(query="", region="both", limit=100)
    assert scope.upstream == []
    assert scope.cache_hits >= 2


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
