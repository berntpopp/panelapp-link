"""FastMCP middleware that converts pre-body argument-validation failures into
the structured ``invalid_input`` envelope.

FastMCP validates tool arguments (Pydantic ``TypeAdapter``) inside
``FunctionTool.run`` *before* the tool body runs, so an invalid ``response_mode``
or an unknown argument name raises a validation error where the body's
``run_mcp_tool`` boundary can never catch it. FastMCP (3.4.x) re-wraps pydantic's
call-validation error as its OWN ``fastmcp.exceptions.ValidationError`` (with the
pydantic error as ``__cause__``); this middleware catches BOTH that and a bare
``pydantic.ValidationError`` and returns a normal ``ToolResult`` whose structured
content is the same ``invalid_input`` envelope a domain error would produce -- so
*every* error the client sees is chainable, never a raw Pydantic/JSON-RPC dump
(which would leak the attacker-chosen argument name and rejected input).
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError as FastMCPResourceError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.resources.base import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError as PydanticValidationError

from panelapp_link.mcp.envelope import (
    arg_validation_failure_envelope,
    unknown_tool_envelope,
    validation_error_envelope,
)


class InputValidationMiddleware(Middleware):
    """Re-wrap argument-validation errors as a structured ``invalid_input`` envelope."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            # Unknown/disabled tool: the requested NAME is attacker-controlled and
            # FastMCP would echo it verbatim in caller-visible TextContent. Emit a
            # fixed, name-free envelope (tool redacted in _meta) instead.
            return ToolResult(structured_content=unknown_tool_envelope())
        except (FastMCPValidationError, PydanticValidationError) as exc:
            tool_name = context.message.name
            arguments = dict(context.message.arguments or {})
            cause = exc if isinstance(exc, PydanticValidationError) else exc.__cause__
            if isinstance(cause, PydanticValidationError):
                envelope = validation_error_envelope(
                    tool_name=tool_name, arguments=arguments, exc=cause
                )
            else:
                envelope = arg_validation_failure_envelope(tool_name=tool_name, arguments=arguments)
            return ToolResult(structured_content=envelope)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ResourceResult],
    ) -> ResourceResult:
        """Emit FIXED, URI-free errors for an unknown or unreadable resource.

        FastMCP raises ``NotFoundError("Unknown resource: '<uri>'")`` (and
        ``ResourceError("Error reading resource '<uri>': <detail>")``) which echo
        the attacker-controlled URI (and error detail) into the caller-visible
        ``McpError``. Re-raise a fixed message that names neither.
        """
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            raise FastMCPNotFoundError("The requested resource was not found.") from None
        except FastMCPResourceError:
            raise FastMCPResourceError("The resource could not be read.") from None


class _ValidationLogScrubFilter(logging.Filter):
    """Scrub FastMCP's pre-middleware argument-validation log records.

    FastMCP logs the full pydantic error (which embeds the caller-chosen argument
    NAMES and rejected input VALUES -- including control/zero-width/bidi/NUL code
    points) at WARNING *before* this middleware converts the failure into a
    structured envelope. Replace that record's payload with fixed metadata so
    caller input never reaches a log/telemetry sink (PII / M3 invariant).
    """

    _MARK = "Invalid arguments for tool"

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and record.msg.startswith(self._MARK):
            record.msg = "Invalid arguments for a tool call (details omitted)."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


_LOG_SCRUB_FILTER = _ValidationLogScrubFilter()


def install_validation_log_filter() -> None:
    """Attach the arg-validation log scrubber to FastMCP's server logger (idempotent)."""
    logger = logging.getLogger("fastmcp.server.server")
    if not any(isinstance(f, _ValidationLogScrubFilter) for f in logger.filters):
        logger.addFilter(_LOG_SCRUB_FILTER)
