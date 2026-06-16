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
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from panelapp_link.constants import (
    CITATION_SHORT,
    DATA_LICENSE,
    RECOMMENDED_CITATION_AU,
    RECOMMENDED_CITATION_UK,
)
from panelapp_link.exceptions import (
    AmbiguousQueryError,
    DataUnavailableError,
    DownloadError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
)
from panelapp_link.mcp.next_commands import recovery_commands

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
    """Return (error_code, client_safe_message, retryable)."""
    if isinstance(exc, McpToolError):
        return exc.error_code, exc.message, exc.error_code in _RETRYABLE_CODES
    if isinstance(exc, RateLimitError):
        return "rate_limited", "PanelApp API rate limit hit. Try again later.", True
    if isinstance(exc, DownloadError):
        return "upstream_unavailable", "Could not reach the PanelApp API. Try again later.", True
    if isinstance(exc, DataUnavailableError):
        return (
            "data_unavailable",
            "PanelApp database not built. Run `panelapp-link-data build`.",
            False,
        )
    if isinstance(exc, NotFoundError):
        return "not_found", str(exc), False
    if isinstance(exc, AmbiguousQueryError):
        return "ambiguous_query", str(exc), False
    if isinstance(exc, InvalidInputError):
        msg = f"Invalid input -- `{exc.field}`: {exc.message}" if exc.field else exc.message
        return "invalid_input", msg, False
    if isinstance(exc, PydanticValidationError):
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "input"
        return "invalid_input", f"Invalid input -- `{loc}`: {first['msg']}", False
    return "internal_error", "An internal error occurred. The request was not completed.", False


def _recovery_action(error_code: str) -> str:
    if error_code in {"rate_limited", "upstream_unavailable"}:
        return "retry_backoff"
    if error_code == "data_unavailable":
        return "build_database"
    if error_code == "invalid_input":
        return "reformulate_input"
    if error_code in {"not_found", "ambiguous_query"}:
        return "switch_tool"
    return "retry_backoff"


def _field_errors(exc: BaseException) -> list[dict[str, str]] | None:
    if isinstance(exc, InvalidInputError) and exc.field:
        return [{"field": exc.field, "reason": exc.message}]
    if isinstance(exc, PydanticValidationError):
        return [
            {"field": ".".join(str(p) for p in e["loc"]) or "input", "reason": e["msg"]}
            for e in exc.errors()
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
    if field_name is None and isinstance(exc, PydanticValidationError):
        errs = exc.errors()
        if errs and errs[0]["loc"]:
            field_name = str(errs[0]["loc"][-1])
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
    return envelope


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
    return _error_envelope(exc, ctx, request_id=uuid.uuid4().hex[:12], elapsed_ms=0.0)


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
    try:
        result = await call()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        if isinstance(result, dict):
            result.setdefault("success", True)
            existing_meta: dict[str, Any] = result.get("_meta") or {}
            result["_meta"] = {
                **existing_meta,
                **_provenance_meta(response_mode),
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
            }
        return result
    except Exception as exc:  # broad catch is the error-boundary contract
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        envelope = _error_envelope(exc, ctx, request_id=request_id, elapsed_ms=elapsed_ms)
        logger.warning(
            "mcp_tool_error tool=%s code=%s exc=%s",
            tool_name,
            envelope["error_code"],
            exc.__class__.__name__,
        )
        return envelope
