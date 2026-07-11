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

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from panelapp_link.config import settings
from panelapp_link.mcp.untrusted_content import FORBIDDEN_CODEPOINTS

logger = logging.getLogger(__name__)

_TRACER = trace.get_tracer("panelapp-link")

# Span attribute keys FastMCP core fills with the caller's OWN requested tool name /
# resource URI / prompt name. On a not-found dispatch these carry attacker-controlled
# prose + forbidden code points into a recording span; the redactor replaces them.
_CALLER_ATTR_KEYS = (
    "fastmcp.component.key",
    "mcp.resource.uri",
    "gen_ai.tool.name",
    "gen_ai.prompt.name",
)
_REDACTED = "<redacted>"

# Cache the lazily-built span-processor class (the opentelemetry-sdk import is only
# available under the `otel` extra / dev group; the base runtime ships api-only).
_REDACTOR_CLASS: type | None = None
_REDACTOR_IMPORT_FAILED = False


def _has_forbidden_codepoint(value: str) -> bool:
    return any(ord(ch) in FORBIDDEN_CODEPOINTS for ch in value)


def _redact_span_in_place(span: Any) -> None:
    """Strip caller-supplied name/URI + forbidden code points from a recording span.

    Only touches spans that (a) errored (an ``exception`` event or ERROR status) AND
    (b) carry a FastMCP-core caller attribute -- i.e. exactly the unknown
    tool/resource/prompt dispatch spans. panelapp-link's own spans (``mcp.tool/…`` /
    ``panelapp.api/…``) and every successful FastMCP span are left untouched, so
    legitimate observability is preserved.
    """
    attrs = dict(span.attributes or {})
    events = getattr(span, "_events", None) or []
    has_exception = any(getattr(e, "name", "") == "exception" for e in events)
    status = getattr(span, "_status", None)
    is_error = has_exception or (
        status is not None and getattr(status, "status_code", None) == StatusCode.ERROR
    )
    carries_caller = any(key in attrs for key in _CALLER_ATTR_KEYS)
    if not (is_error and carries_caller):
        return

    # 1. Name -> the fixed MCP method (a safe enum like "tools/call"), never "<method>
    #    <caller-name>".
    method = attrs.get("mcp.method.name")
    span._name = method if isinstance(method, str) and method else "mcp.request"

    # 2. Attributes -> redact the known caller-bearing keys; defensively redact any
    #    other string value that still carries a forbidden code point.
    new_attrs: dict[str, Any] = {}
    for key, value in attrs.items():
        if key in _CALLER_ATTR_KEYS or (isinstance(value, str) and _has_forbidden_codepoint(value)):
            new_attrs[key] = _REDACTED
        else:
            new_attrs[key] = value
    span._attributes = new_attrs

    # 3. Drop the exception event(s): exception.message / exception.stacktrace echo the
    #    caller name/URI verbatim.
    if events:
        kept = [e for e in events if getattr(e, "name", "") != "exception"]
        if len(kept) != len(events):
            # Assign a plain list: the SDK's BoundedList takes a maxlen (not an
            # iterable) in its constructor, and exporters only iterate ``span.events``.
            span._events = kept

    # 4. Clear the ERROR status description (``str(exc)`` echoes the name/URI).
    if status is not None and getattr(status, "description", None):
        span._status = Status(status.status_code)


def _redactor_class() -> type | None:
    """Return the SDK-backed span-processor class, or None if the SDK is absent."""
    global _REDACTOR_CLASS, _REDACTOR_IMPORT_FAILED
    if _REDACTOR_CLASS is not None or _REDACTOR_IMPORT_FAILED:
        return _REDACTOR_CLASS
    try:
        from opentelemetry.sdk.trace import SpanProcessor
    except Exception:  # pragma: no cover - api-only deploy: nothing to redact
        _REDACTOR_IMPORT_FAILED = True
        return None

    class _ExceptionSpanRedactor(SpanProcessor):
        """Span processor that scrubs caller-reflecting not-found spans before export.

        Must run BEFORE any exporter so a synchronous ``SimpleSpanProcessor`` exports
        the already-scrubbed span; ``setup_tracing`` adds it as the first processor.
        """

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            return None

        def on_end(self, span: Any) -> None:
            _redact_span_in_place(span)

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    _REDACTOR_CLASS = _ExceptionSpanRedactor
    return _REDACTOR_CLASS


def install_span_exception_redactor(provider: Any = None) -> bool:
    """Attach the not-found span redactor to a tracer provider (default: the global).

    Returns False (no-op) when opentelemetry-sdk is not installed or the provider has
    no ``add_span_processor`` (a non-recording API-only provider). Idempotent per
    provider. Call this BEFORE registering exporters so the redactor scrubs first.
    """
    redactor_cls = _redactor_class()
    if redactor_cls is None:
        return False
    target = provider if provider is not None else trace.get_tracer_provider()
    add = getattr(target, "add_span_processor", None)
    if not callable(add):
        return False
    active = getattr(target, "_active_span_processor", None)
    existing = getattr(active, "_span_processors", ()) if active is not None else ()
    if any(type(p).__name__ == "_ExceptionSpanRedactor" for p in existing):
        return True
    add(redactor_cls())
    return True


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


def setup_tracing() -> bool:
    """Install an OTLP TracerProvider when PANELAPP_LINK_OTEL__ENABLED is set.

    No-op (returns False) when disabled or when the SDK/exporter is not
    installed. The console exporter is stderr-only and suppressed under stdio so
    it can never corrupt the MCP JSON-RPC channel.
    """
    if not settings.otel.enabled:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL enabled but opentelemetry-sdk/exporter missing; tracing stays no-op")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": "panelapp-link"}))
    # Install the not-found span redactor FIRST so it scrubs a caller-reflecting span
    # before any exporter (OTLP batch or the synchronous console) reads it.
    install_span_exception_redactor(provider)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if settings.otel.console and settings.transport != "stdio":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing enabled (OTLP)")
    return True
