"""Process-wide RED metrics for PanelApp-Link.

RED = **R**ate (requests), **E**rrors (by code), **D**uration (percentiles), the
aggregation layer the per-call ``_meta`` breadcrumbs cannot give you: with this
you can see the *system*, not just one call. Plus a cache hit ratio and
per-region upstream timing so the dominant cost (the cold double-fetch) is
visible.

Exported two ways: Prometheus text at ``/metrics`` (no extra dependency -- the
0.0.4 exposition format is rendered by hand) and a JSON snapshot folded into
``get_panelapp_diagnostics``. Counters are lifetime; percentiles are computed over
a bounded recent window so memory stays flat under sustained load.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from typing import Any

_WINDOW = 2048  # recent samples retained per histogram for percentile estimates


def _percentile(ordered: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list (0.0 when empty)."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = math.ceil(pct / 100.0 * len(ordered))
    idx = min(max(rank - 1, 0), len(ordered) - 1)
    return round(ordered[idx], 2)


class _Histogram:
    """Lifetime count/sum plus a bounded ring buffer for percentile snapshots."""

    __slots__ = ("_samples", "count", "total")

    def __init__(self) -> None:
        self._samples: deque[float] = deque(maxlen=_WINDOW)
        self.count = 0
        self.total = 0.0

    def observe(self, value: float) -> None:
        self._samples.append(value)
        self.count += 1
        self.total += value

    def snapshot(self) -> dict[str, float | int]:
        ordered = sorted(self._samples)
        return {
            "count": self.count,
            "sum": round(self.total, 2),
            "p50": _percentile(ordered, 50),
            "p95": _percentile(ordered, 95),
            "p99": _percentile(ordered, 99),
            "max": round(ordered[-1], 2) if ordered else 0.0,
        }


class MetricsRegistry:
    """Thread-safe in-process RED registry (one per process by default)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, int] = defaultdict(int)
        self._errors: dict[tuple[str, str], int] = defaultdict(int)
        self._cache: dict[str, int] = defaultdict(int)
        self._tool_durations: dict[str, _Histogram] = defaultdict(_Histogram)
        self._upstream_durations: dict[str, _Histogram] = defaultdict(_Histogram)

    # --- recording ------------------------------------------------------

    def record_request(self, tool: str, error_code: str | None, duration_ms: float) -> None:
        """Record one tool invocation: rate, optional error-by-code, and duration."""
        with self._lock:
            self._requests[tool] += 1
            if error_code is not None:
                self._errors[(tool, error_code)] += 1
            self._tool_durations[tool].observe(duration_ms)

    def record_cache(self, result: str) -> None:
        """Record one cache event (``hit`` | ``miss`` | ``coalesced``)."""
        with self._lock:
            self._cache[result] += 1

    def record_upstream(self, region: str, duration_ms: float) -> None:
        """Record one upstream region fetch duration."""
        with self._lock:
            self._upstream_durations[region].observe(duration_ms)

    def reset(self) -> None:
        """Drop all counters/histograms (test isolation)."""
        with self._lock:
            self._requests.clear()
            self._errors.clear()
            self._cache.clear()
            self._tool_durations.clear()
            self._upstream_durations.clear()

    # --- reporting ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-ready RED snapshot for diagnostics."""
        with self._lock:
            hits = self._cache.get("hit", 0)
            misses = self._cache.get("miss", 0)
            denom = hits + misses
            cache = {
                "hit": hits,
                "miss": misses,
                "coalesced": self._cache.get("coalesced", 0),
                "hit_ratio": round(hits / denom, 4) if denom else None,
            }
            errors_by_code: dict[str, int] = defaultdict(int)
            for (_tool, code), count in self._errors.items():
                errors_by_code[code] += count
            return {
                "requests_total": sum(self._requests.values()),
                "requests_by_tool": dict(self._requests),
                "errors_total": sum(self._errors.values()),
                "errors_by_code": dict(errors_by_code),
                "cache": cache,
                "tool_duration_ms": {
                    tool: hist.snapshot() for tool, hist in self._tool_durations.items()
                },
                "upstream_duration_ms": {
                    region: hist.snapshot() for region, hist in self._upstream_durations.items()
                },
            }

    def render_prometheus(self) -> str:
        """Render the snapshot as Prometheus text exposition (format 0.0.4)."""
        with self._lock:
            lines: list[str] = []
            lines.append("# HELP panelapp_requests_total Total MCP tool invocations.")
            lines.append("# TYPE panelapp_requests_total counter")
            for tool, count in sorted(self._requests.items()):
                lines.append(f'panelapp_requests_total{{tool="{tool}"}} {count}')

            lines.append("# HELP panelapp_errors_total Tool errors by code.")
            lines.append("# TYPE panelapp_errors_total counter")
            for (tool, code), count in sorted(self._errors.items()):
                lines.append(f'panelapp_errors_total{{tool="{tool}",code="{code}"}} {count}')

            lines.append("# HELP panelapp_cache_events_total Cache events by result.")
            lines.append("# TYPE panelapp_cache_events_total counter")
            for result, count in sorted(self._cache.items()):
                lines.append(f'panelapp_cache_events_total{{result="{result}"}} {count}')

            _render_summary(
                lines,
                "panelapp_tool_duration_ms",
                "MCP tool call duration in milliseconds.",
                "tool",
                self._tool_durations,
            )
            _render_summary(
                lines,
                "panelapp_upstream_duration_ms",
                "Upstream PanelApp fetch duration in milliseconds, by region.",
                "region",
                self._upstream_durations,
            )
        return "\n".join(lines) + "\n"


def _render_summary(
    lines: list[str],
    metric: str,
    help_text: str,
    label: str,
    histograms: dict[str, _Histogram],
) -> None:
    """Append a Prometheus ``summary`` (p50/p95/p99 quantiles + _count/_sum)."""
    lines.append(f"# HELP {metric} {help_text}")
    lines.append(f"# TYPE {metric} summary")
    for key, hist in sorted(histograms.items()):
        snap = hist.snapshot()
        for quantile, stat in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99")):
            lines.append(f'{metric}{{{label}="{key}",quantile="{quantile}"}} {snap[stat]}')
        lines.append(f'{metric}_count{{{label}="{key}"}} {snap["count"]}')
        lines.append(f'{metric}_sum{{{label}="{key}"}} {snap["sum"]}')


_REGISTRY = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    """Return the process-wide metrics registry."""
    return _REGISTRY


def reset_metrics() -> None:
    """Reset the process-wide registry (test isolation)."""
    _REGISTRY.reset()
