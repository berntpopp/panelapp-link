"""MCP-layer rate limiting for PanelApp-Link.

The server is read-only over public upstream data, so it ships without auth. But
an unthrottled client can still induce heavy UK+AU upstream fan-out and trigger
HTTP 429s for *everyone*. An opt-in per-process token bucket
(``PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE``) caps the tool-call rate; over the
cap the call is rejected with a structured ``rate_limited`` envelope and never
reaches the upstream. Disabled by default (``per_minute = 0``) so the default
deployment is unchanged.

Single-process deployment means a single in-memory bucket is the whole picture;
a multi-replica deployment would front this with a shared limiter (e.g. at the
reverse proxy).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from panelapp_link.mcp.envelope import rate_limited_envelope


class TokenBucket:
    """A monotonic-clock token bucket (capacity tokens, refilled per second)."""

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        *,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill = refill_per_sec
        self._time = time_fn
        self._tokens = float(capacity)
        self._last = time_fn()

    def allow(self) -> bool:
        """Consume one token if available; return whether the call is permitted."""
        now = self._time()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class RateLimitMiddleware(Middleware):
    """Reject tool calls above ``per_minute`` with a structured ``rate_limited``."""

    def __init__(self, per_minute: int, *, burst: int | None = None) -> None:
        if per_minute <= 0:
            self._bucket: TokenBucket | None = None
        else:
            capacity = burst if burst is not None else per_minute
            self._bucket = TokenBucket(capacity=capacity, refill_per_sec=per_minute / 60.0)

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        if self._bucket is None or self._bucket.allow():
            return await call_next(context)
        envelope = rate_limited_envelope(context.message.name)
        return ToolResult(structured_content=envelope)
