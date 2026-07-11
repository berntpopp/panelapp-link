"""OTel recording-span proof for the FastMCP-core not-found guard.

panelapp-link is the ONE fleet backend that LOCKS ``opentelemetry-sdk`` as a hard
dependency, so the OpenTelemetry span surface is *reachable*: a deployment can (and
the OTLP-enabled deployment does) configure a RECORDING ``TracerProvider`` + exporter.
FastMCP's core ``server_span`` runs INSIDE dispatch -- it puts the caller-supplied
tool name / resource URI / prompt name into the span NAME and into caller-controlled
attributes (``fastmcp.component.key``, ``mcp.resource.uri``, ``gen_ai.tool.name``,
``gen_ai.prompt.name``), and on the not-found exception calls ``record_exception()``
(an ``exception`` event whose ``exception.message`` / ``exception.stacktrace`` echo the
name) and ``set_status(Status(ERROR, str(exc)))``. All of that is captured BEFORE the
backend's caller-frame guard can intervene.

This test configures an in-memory recording provider with the span-exception redactor
installed (as the outermost/first processor, exactly as ``setup_tracing`` wires it in
production) and drives the hostile unknown tool / resource / prompt vectors, then
asserts NO exported span -- name, attributes, events, or status -- carries the hostile
prose or the forbidden code points. This is the proof the OTel leak is closed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from fastmcp import Client
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing
from panelapp_link.observability.tracing import install_span_exception_redactor

# Reuse the exact hostile corpus + leak assertion from the sibling guard test.
from tests.unit.mcp.test_notfound_guard import (
    HOSTILE_PROMPT_NAME,
    HOSTILE_TOOL_NAME,
    _assert_no_leak,
)

# A VALID AnyUrl unknown URI (no forbidden code points) so it actually reaches the
# resource read handler and a recording span is created -- a forbidden-code-point URI
# is rejected at session deserialization before any span exists.
HOSTILE_VALID_UNKNOWN_URI = "panelapp://evil-no_such-IGNORE_ALL_PREVIOUS-resource"


@pytest.fixture(autouse=True)
def _reset_service() -> Iterator[None]:
    reset_panelapp_service()
    set_service_for_testing(None)
    try:
        yield
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


@pytest.fixture
def recording_exporter() -> Iterator[InMemorySpanExporter]:
    """A RECORDING global TracerProvider with the redactor as the first processor.

    Order matters: the redactor must run before the exporter so a synchronous
    ``SimpleSpanProcessor`` exports the already-scrubbed span -- this mirrors how
    ``setup_tracing`` prepends the redactor ahead of the OTLP/console exporters.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    install_span_exception_redactor(provider)  # FIRST processor
    provider.add_span_processor(SimpleSpanProcessor(exporter))  # exporter second
    saved = otel_trace._TRACER_PROVIDER  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    try:
        yield exporter
    finally:
        otel_trace._TRACER_PROVIDER = saved  # type: ignore[attr-defined]
        provider.shutdown()


def _span_blob(exporter: InMemorySpanExporter) -> str:
    """Serialize every exported SERVER-side span's name, attributes, events, status.

    Client-side spans are excluded on purpose: in the real fleet the MCP client is a
    separate process (the host/LLM), so the panelapp server cannot -- and need not --
    scrub the client's own outgoing ``tools/call <name>`` span. This mirrors the
    log-capture exclusion of ``fastmcp.client`` / ``mcp.client`` loggers. Server spans
    are identified by the ``fastmcp.server.name`` attribute that ``server_span`` always
    sets (and that the redactor never strips)."""
    parts: list[str] = []
    for span in exporter.get_finished_spans():
        attrs = dict(span.attributes or {})
        if "fastmcp.server.name" not in attrs:
            continue  # client-side span -- out of the server's trust boundary
        parts.append(span.name)
        parts.append(json.dumps(attrs, ensure_ascii=False))
        for event in span.events:
            parts.append(event.name)
            parts.append(json.dumps(dict(event.attributes or {}), ensure_ascii=False))
        if span.status is not None and span.status.description:
            parts.append(span.status.description)
    return " || ".join(parts)


def _server_span_names(exporter: InMemorySpanExporter) -> list[str]:
    return [
        s.name
        for s in exporter.get_finished_spans()
        if "fastmcp.server.name" in dict(s.attributes or {})
    ]


async def _raw_request(method: str, params: dict[str, Any]) -> None:
    """Drive one raw JSON-RPC request (resources/read, prompts/get) so a recording
    span is created around the not-found dispatch."""
    mcp = create_panelapp_mcp()
    srv = mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lambda: srv.run(
                    server_read,
                    server_write,
                    srv.create_initialization_options(),
                    stateless=False,
                    raise_exceptions=False,
                )
            )

            async def send(obj: Any) -> None:
                await client_write.send(SessionMessage(JSONRPCMessage(obj)))

            async def recv(req_id: int) -> None:
                with anyio.move_on_after(3.0):
                    async for msg in client_read:
                        root = msg.message.root if not isinstance(msg, Exception) else None
                        if root is not None and getattr(root, "id", None) == req_id:
                            return
                return

            await send(
                JSONRPCRequest(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params={
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "hostile", "version": "0"},
                    },
                )
            )
            await recv(1)
            await send(
                JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized", params={})
            )
            await send(JSONRPCRequest(jsonrpc="2.0", id=42, method=method, params=params))
            await recv(42)
            tg.cancel_scope.cancel()


async def test_recording_span_unknown_tool_is_redacted(
    recording_exporter: InMemorySpanExporter,
) -> None:
    """An unknown, hostile tool name leaves no hostile trace in any SERVER span.

    For the tool surface the Layer-1 registry preflight short-circuits BEFORE FastMCP
    core's ``server_span`` is created, so no server-side ``tools/call <name>`` span is
    ever recorded (the only ``tools/call`` span here is the in-process client's, which
    is out of the server's trust boundary)."""
    mcp = create_panelapp_mcp()
    async with Client(mcp) as client:
        await client.call_tool(HOSTILE_TOOL_NAME, {}, raise_on_error=False)
    _assert_no_leak(_span_blob(recording_exporter))
    # The server never created a tools/call span for the unknown tool (preflight).
    assert "tools/call" not in " ".join(_server_span_names(recording_exporter))


async def test_recording_span_unknown_resource_is_redacted(
    recording_exporter: InMemorySpanExporter,
) -> None:
    """A valid-but-unknown resource URI (which DOES create a recording resource span)
    leaves no hostile trace in the span name, attributes, events, or status."""
    await _raw_request("resources/read", {"uri": HOSTILE_VALID_UNKNOWN_URI})
    blob = _span_blob(recording_exporter)
    # Sanity: a recording resource span really was produced (otherwise the test is vacuous).
    assert "resources/read" in blob
    _assert_no_leak(blob)


async def test_recording_span_unknown_prompt_is_redacted(
    recording_exporter: InMemorySpanExporter,
) -> None:
    """An unknown, hostile prompt name (captured in the span name + attributes +
    exception event) leaves no hostile trace in any exported span."""
    await _raw_request("prompts/get", {"name": HOSTILE_PROMPT_NAME, "arguments": {}})
    blob = _span_blob(recording_exporter)
    assert "prompts/get" in blob
    _assert_no_leak(blob)
