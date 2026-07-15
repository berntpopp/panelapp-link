"""Tests for MCP-layer rate limiting (panelapp_link.mcp.rate_limit).

A public host with no auth can let one client induce heavy UK+AU upstream
fan-out and trigger 429s for everyone. An opt-in per-process token bucket caps
the tool-call rate and returns a structured ``rate_limited`` envelope (never an
upstream call) when exceeded.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from panelapp_link.mcp.envelope import rate_limited_envelope
from panelapp_link.mcp.rate_limit import RateLimitMiddleware, TokenBucket
from panelapp_link.observability.metrics import get_metrics, reset_metrics


def test_token_bucket_allows_up_to_capacity_then_denies() -> None:
    clock = [0.0]
    bucket = TokenBucket(capacity=2, refill_per_sec=0.0, time_fn=lambda: clock[0])
    assert bucket.allow() is True
    assert bucket.allow() is True
    assert bucket.allow() is False  # capacity exhausted, no refill


def test_token_bucket_refills_over_time() -> None:
    clock = [0.0]
    bucket = TokenBucket(capacity=1, refill_per_sec=1.0, time_fn=lambda: clock[0])
    assert bucket.allow() is True
    assert bucket.allow() is False
    clock[0] = 1.0  # one second -> one token refilled
    assert bucket.allow() is True


def test_rate_limited_envelope_shape() -> None:
    reset_metrics()
    env = rate_limited_envelope("search_panels")
    assert env["success"] is False
    assert env["error_code"] == "rate_limited"
    assert env["retryable"] is True
    assert env["recovery_action"] == "retry_backoff"
    assert env["_meta"]["tool"] == "search_panels"
    assert get_metrics().snapshot()["errors_by_code"]["rate_limited"] == 1


def _ctx(tool: str) -> Any:
    return SimpleNamespace(message=SimpleNamespace(name=tool, arguments={}))


async def test_middleware_disabled_passes_through() -> None:
    mw = RateLimitMiddleware(per_minute=0)
    sentinel = object()

    async def call_next(_ctx: Any) -> Any:
        return sentinel

    result = await mw.on_call_tool(_ctx("search_panels"), call_next)
    assert result is sentinel


async def test_middleware_blocks_after_capacity() -> None:
    reset_metrics()
    mw = RateLimitMiddleware(per_minute=60, burst=1)
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return SimpleNamespace(structured_content={"success": True})

    first = await mw.on_call_tool(_ctx("search_panels"), call_next)
    second = await mw.on_call_tool(_ctx("search_panels"), call_next)

    assert first.structured_content["success"] is True
    assert second.structured_content["success"] is False
    assert second.structured_content["error_code"] == "rate_limited"
    assert calls == 1  # the blocked call never reached the tool body


async def test_facade_rate_limits_when_configured(live_service: Any) -> None:
    from fastmcp import Client

    from panelapp_link.config import settings
    from panelapp_link.mcp.facade import create_panelapp_mcp
    from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing

    original = settings.mcp_rate_limit_per_minute
    settings.mcp_rate_limit_per_minute = 1  # cap at 1/min, burst 1
    set_service_for_testing(live_service)
    try:
        mcp = create_panelapp_mcp()
        async with Client(mcp) as client:
            first = (await client.call_tool("search_panels", {"region": "uk"})).structured_content
            second = await client.call_tool(
                "get_server_capabilities", {}, raise_on_error=False
            )
        assert first["success"] is True
        assert second.is_error is True  # rate-limit rejection carries isError:true
        assert second.structured_content["success"] is False
        assert second.structured_content["error_code"] == "rate_limited"
    finally:
        settings.mcp_rate_limit_per_minute = original
        set_service_for_testing(None)
        reset_panelapp_service()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
