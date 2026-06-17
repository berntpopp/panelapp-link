"""Observability for PanelApp-Link: RED metrics, per-request telemetry, traces.

Three cooperating layers, mirroring the 2026 MCP observability baseline:

- :mod:`~panelapp_link.observability.metrics` -- process-wide RED aggregates
  (request rate, error rate by code, tool/upstream duration percentiles, cache
  hit ratio) exported as Prometheus text and surfaced in diagnostics.
- :mod:`~panelapp_link.observability.telemetry` -- per-call ``ContextVar`` scope
  (cache hit/miss/coalesced, per-region upstream timing) folded into ``_meta``.
- :mod:`~panelapp_link.observability.tracing` -- OpenTelemetry spans wrapping each
  tool call and each upstream region request, correlated by ``request_id``.
"""

from __future__ import annotations
