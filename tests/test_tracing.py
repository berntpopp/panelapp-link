"""Tests for OpenTelemetry tracing (panelapp_link.observability.tracing).

The library instruments with the OTel *API* (a no-op until an SDK + exporter is
configured). These tests configure an in-memory SDK exporter to assert the real
span shape: a tool span per call, an upstream span per region fetch parented
under it (one trace per request), and request_id correlation on every span.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from panelapp_link.observability import tracing

_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def _clear_spans() -> None:
    _EXPORTER.clear()


def test_tool_span_records_name_and_request_id() -> None:
    with tracing.tool_span("search_panels", "req123456789", {"mcp.response_mode": "compact"}):
        pass
    (span,) = _EXPORTER.get_finished_spans()
    assert span.name == "mcp.tool/search_panels"
    assert span.attributes["panelapp.request_id"] == "req123456789"
    assert span.attributes["mcp.tool.name"] == "search_panels"
    assert span.attributes["mcp.response_mode"] == "compact"


def test_upstream_span_is_child_of_tool_span() -> None:
    with tracing.tool_span("get_gene_panels", "rid"):
        with tracing.upstream_span("uk", "genes", "rid"):
            pass
        with tracing.upstream_span("australia", "genes", "rid"):
            pass
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    tool = spans["mcp.tool/get_gene_panels"]
    uk = spans["panelapp.api/genes"]  # both upstreams share the name; grab any
    assert uk.parent is not None
    assert uk.parent.span_id == tool.context.span_id
    assert uk.context.trace_id == tool.context.trace_id
    assert uk.attributes["panelapp.region"] in {"uk", "australia"}
    assert uk.attributes["panelapp.request_id"] == "rid"


def test_record_error_sets_status_and_code() -> None:
    with tracing.tool_span("get_panel", "rid") as span:
        tracing.record_error(span, "not_found")
    (finished,) = _EXPORTER.get_finished_spans()
    assert finished.attributes["panelapp.error_code"] == "not_found"
    assert finished.status.status_code == trace.StatusCode.ERROR
