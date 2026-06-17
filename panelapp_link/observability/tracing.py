"""OpenTelemetry tracing for PanelApp-Link.

We instrument with the OTel *API* only (a declared runtime dependency). Without a
configured SDK + exporter every span is a non-recording no-op, so this is free in
the default deployment; an operator activates real tracing by installing
``opentelemetry-sdk`` plus an exporter and wiring a ``TracerProvider`` (the
standard library-instrumentation pattern).

Two span kinds, correlated by ``request_id`` so one MCP call is one trace:

- :func:`tool_span` wraps a tool call (``mcp.tool/<name>``).
- :func:`upstream_span` wraps one upstream region fetch (``panelapp.api/<endpoint>``)
  and -- because it runs inside the tool span's context -- is a child of it, so a
  13 s call shows both region fetches under a single trace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

_TRACER = trace.get_tracer("panelapp-link")


@contextmanager
def tool_span(
    tool_name: str,
    request_id: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Span for one MCP tool call, tagged with the correlation ``request_id``."""
    with _TRACER.start_as_current_span(f"mcp.tool/{tool_name}") as span:
        span.set_attribute("mcp.tool.name", tool_name)
        span.set_attribute("panelapp.request_id", request_id)
        for key, value in (attributes or {}).items():
            if value is not None and value != "":
                span.set_attribute(key, value)
        yield span


@contextmanager
def upstream_span(region: str, endpoint: str, request_id: str | None) -> Iterator[Span]:
    """Span for one upstream PanelApp region fetch (child of the active tool span)."""
    with _TRACER.start_as_current_span(f"panelapp.api/{endpoint}") as span:
        span.set_attribute("panelapp.region", region)
        span.set_attribute("panelapp.endpoint", endpoint)
        if request_id is not None:
            span.set_attribute("panelapp.request_id", request_id)
        yield span


def record_error(span: Span, error_code: str) -> None:
    """Mark a span as failed and tag it with the structured error code."""
    span.set_attribute("panelapp.error_code", error_code)
    span.set_status(StatusCode.ERROR)
