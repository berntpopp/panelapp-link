"""Shared MCP envelope boundary for PanelApp-Link tools.

Tools return a plain dict; ``run_mcp_tool`` injects ``success``/``_meta`` on the
happy path and converts any exception into a structured error envelope dict
(returned, never raised) so the model sees a structured failure with a stable
``error_code`` instead of an opaque masked message.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from pydantic_core import ErrorDetails

from panelapp_link.constants import (
    CITATION_SHORT,
    DATA_LICENSE,
    RECOMMENDED_CITATION_AU,
    RECOMMENDED_CITATION_UK,
)
from panelapp_link.exceptions import (
    DisallowedURLError,
    DownloadError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    ResponseTooLargeError,
)
from panelapp_link.mcp.next_commands import recovery_commands
from panelapp_link.mcp.untrusted_content import UntrustedTextLimitError, sanitize_message
from panelapp_link.observability import telemetry, tracing
from panelapp_link.observability.metrics import get_metrics

logger = logging.getLogger(__name__)

# Short stable URI for the full citation; emitted instead of the verbatim strings
# in minimal/compact/standard so a warm client dereferences it once and caches.
_CITATION_REF = "panelapp://citation"

# Combined verbatim citation (both regions) for full mode + the unset default.
_RECOMMENDED_CITATION = (
    f"Genomics England PanelApp: {RECOMMENDED_CITATION_UK} "
    f"PanelApp Australia: {RECOMMENDED_CITATION_AU}"
)

# Error codes that are inherently retryable when raised via ``McpToolError``.
_RETRYABLE_CODES = {"rate_limited", "upstream_unavailable"}

# FIXED, error-code-specific public messages for classified exceptions whose OWN
# ``str(exc)`` is built from the caller's query/identifier or an upstream value.
# Code-point stripping is NOT enough for these: injection PROSE
# ("Ignore all previous instructions ...") survives it, so the caller-visible
# message must not interpolate that text at all. The raw detail stays only in the
# server-side exception (never surfaced, never logged verbatim).
_FIXED_MESSAGES: dict[str, str] = {
    "not_found": (
        "The requested PanelApp record was not found. "
        "Use search_panels or resolve_gene to find a valid identifier."
    ),
    # A response-size cap breach is client-actionable (narrow the request), so it is
    # surfaced as invalid_input (the closed enum has no dedicated limit code); the
    # message tells the model exactly how to reformulate.
    "response_too_large": (
        "The response exceeded the untrusted-text size or count limit. "
        "Re-call with a smaller limit or a lower response_mode."
    ),
}

# Fixed, input-free reasons keyed by pydantic error ``type`` -- the pydantic
# ``msg`` (and the rejected input value) can echo caller prose, so it is never
# surfaced. Unlisted types fall back through :func:`_pydantic_reason`.
_PYDANTIC_REASONS: dict[str, str] = {
    "missing": "This required argument is missing.",
    "unexpected_keyword_argument": "Unexpected or unknown argument.",
    "extra_forbidden": "Unexpected or unknown argument.",
    "literal_error": "Value is not one of the allowed options.",
    "enum": "Value is not one of the allowed options.",
}

# Pydantic error types whose ``loc`` LEAF is the caller-chosen (arbitrary) argument
# name; that leaf is redacted rather than echoed.
_UNKNOWN_ARG_TYPES = {"unexpected_keyword_argument", "extra_forbidden"}

# Enum/literal errors: pydantic renders ``ctx['expected']`` from OUR OWN Literal
# members, so it is server-authored -- the one pydantic-supplied string safe to
# surface (unlike ``msg``/``input``, which carry caller prose). Length-capped.
_ENUM_ERROR_TYPES = {"literal_error", "enum"}
_MAX_ALLOWED_CHARS = 160


def _allowed_options(err: ErrorDetails) -> str | None:
    """The allowed values for an enum/literal error, or None for other error types."""
    if str(err.get("type", "")) not in _ENUM_ERROR_TYPES:
        return None
    expected = (err.get("ctx") or {}).get("expected")
    if not isinstance(expected, str) or not expected:
        return None
    return sanitize_message(expected[:_MAX_ALLOWED_CHARS])


def _pydantic_reason(err: ErrorDetails) -> str:
    """Return a FIXED reason for a pydantic arg-validation error.

    Never echoes the pydantic ``msg`` or the rejected input value; both can carry
    caller prose that survives code-point stripping. For an enum/literal error the
    ALLOWED values are appended (server-authored, see ``_allowed_options``) so a
    caller rejected at the schema boundary still learns what to send instead --
    the schema-boundary rejection must not be less instructive than the
    service-layer guidance it now pre-empts.
    """
    etype = str(err.get("type", ""))
    if etype in _PYDANTIC_REASONS:
        reason = _PYDANTIC_REASONS[etype]
        allowed = _allowed_options(err)
        return f"{reason} Allowed: {allowed}." if allowed else reason
    if "parsing" in etype or etype.endswith("_type"):
        return "Wrong type for this argument."
    if "greater" in etype or "less" in etype or "than" in etype:
        return "Value is out of the allowed range."
    if "string" in etype:
        return "Invalid string value for this argument."
    return "Invalid value for this argument."


def _safe_pydantic_field(err: ErrorDetails) -> str:
    """Return a safe field name for a pydantic error.

    A declared-field ``loc`` is server-defined and safe (code-point stripped
    defensively). For an unexpected/unknown keyword argument the loc's LEAF is the
    caller-chosen name -- arbitrary prose that survives code-point stripping -- so it
    is never echoed; only the server-defined PREFIX is kept, which still locates the
    failure (a hostile key inside ``panels[0]`` reports as ``panels.0``). With no safe
    prefix there is nothing left to name: ``<unknown>``.
    """
    loc = tuple(err.get("loc", ()))
    if str(err.get("type", "")) in _UNKNOWN_ARG_TYPES:
        loc = loc[:-1]  # drop the caller-chosen leaf; keep any declared prefix
        if not loc:
            return "<unknown>"
    return sanitize_message(".".join(str(p) for p in loc) or "input")


def _sanitize_tree(value: Any) -> Any:
    """Recursively strip the fence's forbidden code points from every string leaf.

    The final code-point backstop over a WHOLE error envelope -- message, field,
    field_errors, and every ``_meta.next_commands[*].arguments.*`` value -- on top
    of the fixed-message / redaction discipline in :func:`_classify`.
    """
    if isinstance(value, str):
        return sanitize_message(value)
    if isinstance(value, dict):
        return {k: _sanitize_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_tree(v) for v in value]
    return value


@dataclass
class McpErrorContext:
    """Per-call context so envelopes can name the failing tool and build recovery steps."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class McpToolError(Exception):
    """Raised inside a tool body to emit a specific error code/message."""

    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _provenance_meta(response_mode: str | None = None, *, is_error: bool = False) -> dict[str, Any]:
    """Provenance block for ``_meta``; mode-aware to cut per-call tokens.

    - ``unsafe_for_clinical_use`` rides *every* envelope (safety; non-negotiable
      for a clinical-adjacent dataset).
    - Errors carry only ``citation_ref`` -- an error has no claim to cite, so the
      verbatim citation (and even ``citation_short``) is pure boilerplate.
    - ``full`` is the maximum-detail mode: it keeps the verbatim
      ``recommended_citation`` (UK + AU combined) and the ``data_license``.
    - ``minimal``/``compact``/``standard`` carry ``citation_ref`` +
      ``citation_short`` (the short stub already names both sources, so the
      verbatim citation lives at ``panelapp://citation`` / in capabilities).
    """
    meta: dict[str, Any] = {"unsafe_for_clinical_use": True}
    if is_error:
        meta["citation_ref"] = _CITATION_REF
    elif response_mode == "full":
        meta["data_license"] = DATA_LICENSE
        meta["recommended_citation"] = _RECOMMENDED_CITATION
    elif response_mode in ("minimal", "compact", "standard"):
        meta["citation_ref"] = _CITATION_REF
        meta["citation_short"] = CITATION_SHORT
    else:  # unset success default (rare): keep a safe verbatim citation
        meta["recommended_citation"] = _RECOMMENDED_CITATION
    if response_mode:
        meta["response_mode"] = response_mode
    return meta


def _classify(exc: BaseException) -> tuple[str, str, bool]:
    """Return (error_code, client_safe_message, retryable).

    Classified exceptions whose ``str(exc)`` is built from the caller's query /
    identifier or an upstream value NEVER surface that text: a FIXED, error-code
    message is used instead (see ``_FIXED_MESSAGES``). Server-authored strings
    (``InvalidInputError``/``McpToolError`` guidance) are surfaced but stripped of
    forbidden code points by the recursive envelope pass.
    """
    if isinstance(exc, McpToolError):
        return exc.error_code, exc.message, exc.error_code in _RETRYABLE_CODES
    if isinstance(exc, (DisallowedURLError, ResponseTooLargeError)):
        # A blocked outbound URL/redirect (F-17) is a fixed, opaque, NON-retryable
        # failure: retrying re-issues the identical blocked request. The blocked
        # URL/host is never surfaced (the exception message is already fixed).
        return "internal", "An internal error occurred. The request was not completed.", False
    if isinstance(exc, RateLimitError):
        return "rate_limited", "PanelApp API rate limit hit. Try again later.", True
    if isinstance(exc, DownloadError):
        return "upstream_unavailable", "Could not reach the PanelApp API. Try again later.", True
    if isinstance(exc, NotFoundError):
        # str(exc) embeds the caller's query/identifier -> use the fixed message.
        return "not_found", _FIXED_MESSAGES["not_found"], False
    if isinstance(exc, UntrustedTextLimitError):
        # Response-Envelope v1.1 forbids silent omission on a limit breach. The error
        # code is closed to the six-value enum, which has no dedicated limit code, so
        # this maps to invalid_input (client-actionable: narrow the request) -- never a
        # generic, non-actionable internal.
        return "invalid_input", _FIXED_MESSAGES["response_too_large"], False
    if isinstance(exc, InvalidInputError):
        # exc.message is server-authored (static guidance; the rejected value is not
        # interpolated at the raise sites). exc.field is a fixed schema field name.
        msg = f"Invalid input -- `{exc.field}`: {exc.message}" if exc.field else exc.message
        return "invalid_input", msg, False
    if isinstance(exc, PydanticValidationError):
        first = exc.errors()[0]
        field = _safe_pydantic_field(first)
        return "invalid_input", f"Invalid input -- `{field}`: {_pydantic_reason(first)}", False
    return "internal", "An internal error occurred. The request was not completed.", False


def _recovery_action(error_code: str) -> str:
    if error_code in {"rate_limited", "upstream_unavailable"}:
        return "retry_backoff"
    if error_code == "invalid_input":
        return "reformulate_input"
    if error_code == "not_found":
        return "switch_tool"
    return "retry_backoff"


def _field_errors(exc: BaseException) -> list[dict[str, str]] | None:
    if isinstance(exc, InvalidInputError) and exc.field:
        # Both are server-authored; the recursive pass strips code points anyway.
        return [{"field": sanitize_message(exc.field), "reason": sanitize_message(exc.message)}]
    if isinstance(exc, PydanticValidationError):
        # Redact an attacker-chosen argument name; map the pydantic type -> a fixed
        # reason (never echo the pydantic ``msg`` or the rejected input value).
        return [
            {"field": _safe_pydantic_field(e), "reason": _pydantic_reason(e)} for e in exc.errors()
        ]
    return None


def _error_envelope(
    exc: BaseException,
    context: McpErrorContext,
    *,
    request_id: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    error_code, message, retryable = _classify(exc)
    field_name = getattr(exc, "field", None)
    if isinstance(field_name, str):
        field_name = sanitize_message(field_name)
    elif field_name is None and isinstance(exc, PydanticValidationError):
        errs = exc.errors()
        if errs:
            field_name = _safe_pydantic_field(errs[0])
    meta: dict[str, Any] = {"tool": context.tool_name, **_provenance_meta(is_error=True)}
    meta["request_id"] = request_id
    meta["elapsed_ms"] = elapsed_ms
    nexts = recovery_commands(context.tool_name, error_code, context.arguments, field_name)
    if nexts:
        meta["next_commands"] = nexts
    envelope: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "recovery_action": _recovery_action(error_code),
        "_meta": meta,
    }
    field_errors = _field_errors(exc)
    if field_errors is not None:
        envelope["field_errors"] = field_errors
    # Final code-point backstop over every string leaf of the whole error envelope
    # (message, field_errors, and any next_commands recovery argument).
    return cast("dict[str, Any]", _sanitize_tree(envelope))


def validation_error_envelope(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    exc: PydanticValidationError,
) -> dict[str, Any]:
    """Structured ``invalid_input`` envelope for a pre-body argument-validation
    failure (caught by the MCP middleware before the tool body runs).

    Mirrors ``_error_envelope`` exactly so an arg-validation failure is
    byte-compatible with a domain ``invalid_input`` raised inside a tool body.
    """
    ctx = McpErrorContext(tool_name=tool_name, arguments=arguments)
    envelope = _error_envelope(exc, ctx, request_id=uuid.uuid4().hex[:12], elapsed_ms=0.0)
    get_metrics().record_request(tool_name, envelope["error_code"], 0.0)
    return envelope


def unknown_tool_envelope() -> dict[str, Any]:
    """Fixed ``not_found`` envelope for an unknown/disabled tool NAME.

    FastMCP raises ``NotFoundError("Unknown tool: '<name>'")`` for an unregistered
    tool and would echo that attacker-controlled name verbatim in caller-visible
    ``TextContent``. This envelope never surfaces the requested name -- neither in
    the message nor in ``_meta.tool`` (redacted to a fixed placeholder).
    """
    ctx = McpErrorContext(tool_name="<unknown>")
    exc = McpToolError(error_code="not_found", message="Unknown tool.")
    envelope = _error_envelope(exc, ctx, request_id=uuid.uuid4().hex[:12], elapsed_ms=0.0)
    get_metrics().record_request("<unknown>", "not_found", 0.0)
    return envelope


def arg_validation_failure_envelope(*, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fixed ``invalid_input`` envelope when arg validation failed without a
    pydantic cause (a FastMCP ``ValidationError`` whose ``__cause__`` is not a
    ``pydantic.ValidationError``). No caller detail is available or surfaced.
    """
    ctx = McpErrorContext(tool_name=tool_name, arguments=arguments)
    exc = McpToolError(error_code="invalid_input", message="The tool arguments were invalid.")
    envelope = _error_envelope(exc, ctx, request_id=uuid.uuid4().hex[:12], elapsed_ms=0.0)
    get_metrics().record_request(tool_name, "invalid_input", 0.0)
    return envelope


def rate_limited_envelope(tool_name: str) -> dict[str, Any]:
    """Structured ``rate_limited`` envelope for an MCP-layer throttle rejection.

    Mirrors a domain ``rate_limited`` error so the client sees a chainable,
    retryable failure (with a ``retry_backoff`` recovery action) instead of an
    upstream call it should never have triggered.
    """
    ctx = McpErrorContext(tool_name=tool_name)
    exc = McpToolError(
        error_code="rate_limited",
        message=(
            "This MCP server is rate-limiting requests to stay polite to the "
            "upstream PanelApp APIs. Retry after a short backoff."
        ),
    )
    envelope = _error_envelope(exc, ctx, request_id=uuid.uuid4().hex[:12], elapsed_ms=0.0)
    get_metrics().record_request(tool_name, "rate_limited", 0.0)
    return envelope


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    """Execute a tool body, returning the result dict or a structured error dict.

    Adds ``_meta.request_id`` + ``_meta.elapsed_ms`` (trace + server timing) and a
    mode-aware citation to every envelope, success or error.
    """
    ctx = context or McpErrorContext(tool_name=tool_name)
    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    metrics = get_metrics()
    with (
        telemetry.request_scope(request_id) as scope,
        tracing.tool_span(
            tool_name, request_id, {"mcp.response_mode": response_mode or ""}
        ) as span,
    ):
        try:
            result = await call()
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            metrics.record_request(tool_name, None, elapsed_ms)
            if isinstance(result, dict):
                result.setdefault("success", True)
                existing_meta: dict[str, Any] = result.get("_meta") or {}
                meta: dict[str, Any] = {
                    **existing_meta,
                    **_provenance_meta(response_mode),
                    **telemetry.telemetry_meta(scope),
                    "request_id": request_id,
                    "elapsed_ms": elapsed_ms,
                }
                # Minimal mode is for sweep/agent-loop workloads: shed per-call
                # token tax -- one next step, and drop upstream timing + the
                # redundant short citation (the citation_ref stub still rides).
                if response_mode == "minimal":
                    for heavy in ("upstream", "upstream_ms", "citation_short"):
                        meta.pop(heavy, None)
                    if meta.get("next_commands"):
                        meta["next_commands"] = meta["next_commands"][:1]
                result["_meta"] = meta
            return result
        except Exception as exc:  # broad catch is the error-boundary contract
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            envelope = _error_envelope(exc, ctx, request_id=request_id, elapsed_ms=elapsed_ms)
            error_code = envelope["error_code"]
            metrics.record_request(tool_name, error_code, elapsed_ms)
            tracing.record_error(span, error_code)
            envelope["_meta"].update(telemetry.telemetry_meta(scope))
            logger.warning(
                "mcp_tool_error tool=%s code=%s exc=%s",
                tool_name,
                error_code,
                exc.__class__.__name__,
            )
            return envelope
