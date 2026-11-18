"""FastMCP middleware + protocol backstop that keep FastMCP-core not-found
reflection (unknown tool name / resource URI / prompt name, and pre-body
argument-validation failures) out of every caller-visible field and log sink.

FastMCP validates tool arguments (Pydantic ``TypeAdapter``) inside
``FunctionTool.run`` *before* the tool body runs, so an invalid ``response_mode``
or an unknown argument name raises a validation error where the body's
``run_mcp_tool`` boundary can never catch it. FastMCP (3.4.x) re-wraps pydantic's
call-validation error as its OWN ``fastmcp.exceptions.ValidationError`` (with the
pydantic error as ``__cause__``); this middleware catches BOTH that and a bare
``pydantic.ValidationError`` and returns a normal ``ToolResult`` whose structured
content is the same ``invalid_input`` envelope a domain error would produce.

FastMCP core also reflects the caller's OWN requested name/URI back BEFORE (or
around) this middleware runs:

* Unknown TOOL name -> ``NotFoundError("Unknown tool: '<name>'")`` (Layer 1
  registry preflight returns a fixed name-free envelope BEFORE core dispatch, which
  also means no recording OTel span is ever created for the unknown tool).
* Unknown/unreadable RESOURCE URI -> ``NotFoundError``/``ResourceError`` echoing the
  URI (Layer 2 ``on_read_resource`` re-raises a fixed URI-free error).
* Unknown PROMPT name -> ``NotFoundError("Unknown prompt: '<name>'")`` raised by the
  low-level ``prompts/get`` handler and echoed to the caller BEFORE any middleware
  can intervene (Layer 3 protocol backstop wraps the raw request handler).

All caller-visible strings here are FIXED constants -- never the requested name/URI,
``str(exc)``, or any upstream detail (sanitation strips code points but NOT injection
prose, so fixed constants are the only safe source).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError as FastMCPResourceError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.resources.base import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND
from pydantic import ValidationError as PydanticValidationError

from panelapp_link.mcp.envelope import (
    arg_validation_failure_envelope,
    unknown_tool_envelope,
    validation_error_envelope,
)

logger = logging.getLogger(__name__)


def _error_tool_result(envelope: dict[str, Any]) -> ToolResult:
    """Wrap a structured error envelope as a ToolResult with MCP ``isError: true``.

    Response-Envelope v1: "isError: true is REQUIRED so clients surface the error to
    the model for self-correction." A returned dict never sets it, so every error
    envelope this middleware emits (and every domain error envelope the tool body
    returns) is routed through this.
    """
    return ToolResult(structured_content=envelope, is_error=True)


def _flag_domain_error(result: ToolResult) -> ToolResult:
    """Re-flag a tool-body error envelope with ``isError: true``.

    A tool body returns a plain error-envelope dict (``success: False``,
    ``error_code``); FastMCP wraps it as a success ToolResult (isError unset). Detect
    that envelope on the way out and re-emit it with isError set, preserving content
    and meta. This is the single chokepoint for EVERY domain error path -- it cannot
    miss a raise site the way per-site wrapping can.
    """
    structured = getattr(result, "structured_content", None)
    if not isinstance(structured, dict):
        return result
    if structured.get("success") is False or structured.get("error_code"):
        if getattr(result, "is_error", False):
            return result
        return ToolResult(
            content=result.content,
            structured_content=structured,
            meta=getattr(result, "meta", None),
            is_error=True,
        )
    return result


#: Fixed, name/URI-free frames for reflection surfaces that bypass the tool envelope.
_UNKNOWN_TOOL_MESSAGE = "Unknown tool."
_UNKNOWN_RESOURCE_MESSAGE = "The requested resource was not found."
_UNREADABLE_RESOURCE_MESSAGE = "The resource could not be read."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."


class InputValidationMiddleware(Middleware):
    """Re-wrap argument-validation + not-found errors as fixed, caller-safe frames."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        # Layer 1 -- registry preflight. ``get_tool`` returns None for an unknown tool;
        # returning the fixed envelope BEFORE ``call_next`` means the requested (attacker
        # -controlled) name never reaches FastMCP core, so it is echoed neither in the
        # caller-visible TextContent NOR captured by the core's recording OTel span
        # (which is created inside the dispatch that ``call_next`` would trigger).
        fctx = getattr(context, "fastmcp_context", None)
        if fctx is not None:
            try:
                tool_obj = await fctx.fastmcp.get_tool(context.message.name)
            except Exception:
                tool_obj = None
            if tool_obj is None:
                logger.warning("mcp_unknown_tool")
                return _error_tool_result(unknown_tool_envelope())
        try:
            result = await call_next(context)
        except FastMCPNotFoundError:
            # Backstop for the raise path when no fastmcp context was available.
            logger.warning("mcp_unknown_tool")
            return _error_tool_result(unknown_tool_envelope())
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
            return _error_tool_result(envelope)
        # A domain error envelope returned by the tool body arrives here as a
        # success-wrapped ToolResult; re-flag it with isError:true.
        return _flag_domain_error(result)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ResourceResult],
    ) -> ResourceResult:
        """Emit FIXED, URI-free errors for an unknown or unreadable resource.

        FastMCP raises ``NotFoundError("Unknown resource: '<uri>'")`` (and
        ``ResourceError("Error reading resource '<uri>': <detail>")``) which echo
        the attacker-controlled URI (and error detail) into the caller-visible
        ``McpError``. Re-raise a fixed message that names neither. The broad final
        ``except`` guarantees no other exception type can smuggle the URI out.
        """
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            logger.warning("mcp_unknown_resource")
            raise FastMCPNotFoundError(_UNKNOWN_RESOURCE_MESSAGE) from None
        except FastMCPResourceError:
            logger.warning("mcp_resource_error")
            raise FastMCPResourceError(_UNREADABLE_RESOURCE_MESSAGE) from None
        except Exception as exc:
            logger.warning("mcp_resource_error type=%s", type(exc).__name__)
            raise FastMCPResourceError(_UNREADABLE_RESOURCE_MESSAGE) from None


# ---------------------------------------------------------------------------
# Layer 3 -- protocol-handler backstop (clinvar/hpo pattern)
# ---------------------------------------------------------------------------
# FastMCP's CORE dispatch reflects the caller-controlled component name/URI verbatim
# when it is unknown -- notably ``Unknown prompt: '<name>'`` (raised by the low-level
# ``prompts/get`` handler, which mcp turns into ``ErrorData(code=0, message=str(exc))``,
# echoing the name to the caller BEFORE any FastMCP middleware can intervene). This
# wraps the raw ``_mcp_server.request_handlers`` for CallTool / GetPrompt as the
# OUTERMOST layer so no requested name (nor its code points) can reach the JSON-RPC
# error frame. FastMCP 4 echoes an unknown resource URI from the low-level read
# handler BEFORE Layer 2 runs, so ``resources/read`` is wrapped here too. All messages
# are fixed server-authored constants.


def _is_structured_envelope(result: CallToolResult) -> bool:
    """True if an isError CallToolResult carries one of OUR JSON envelopes.

    Distinguishes a structured panelapp-link error (already name-free, e.g. the Layer-1
    unknown-tool frame) from a RAW FastMCP dispatch error whose plain text echoes the
    caller-supplied tool name.
    """
    if not result.content:
        return False
    text = getattr(result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except ValueError, TypeError:
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> CallToolResult:
    """A fixed, name-free CallToolResult for an unknown/failed tool dispatch."""
    envelope = unknown_tool_envelope()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


async def _strip_method_not_found_data(ctx: Any, call_next: Any) -> Any:
    """Drop the caller-supplied method name SDK 2 puts in METHOD_NOT_FOUND ``data``."""
    try:
        return await call_next(ctx)
    except MCPError as exc:
        if exc.error.code == METHOD_NOT_FOUND and exc.error.data is not None:
            raise MCPError(code=METHOD_NOT_FOUND, message="Method not found") from None
        raise


def install_protocol_error_handler(mcp: FastMCP) -> None:
    """Wrap the raw tool/prompt request handlers so a FastMCP-core not-found error can
    never reflect the caller-supplied name.

    Install AFTER all tools/resources are registered (so the handlers exist) and as the
    OUTERMOST wrapper on ``tools/call`` / ``resources/read`` / ``prompts/get``.
    """
    lowlevel = mcp._mcp_server
    # Outermost: MCP SDK 2 echoes an unknown JSON-RPC method name in METHOD_NOT_FOUND ``data``.
    lowlevel.middleware.insert(0, _strip_method_not_found_data)

    call_tool = lowlevel.get_request_handler("tools/call")
    if call_tool is not None:

        async def wrapped_call_tool(
            ctx: Any,
            params: Any,
            *,
            _orig: Any = call_tool.handler,
        ) -> Any:
            try:
                result = await _orig(ctx, params)
            except Exception:
                # A registered tool never raises here (run_mcp_tool returns an
                # envelope); any exception is a dispatch-level failure whose message
                # would echo the caller name -- mask it.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            if (
                isinstance(result, CallToolResult)
                and result.is_error
                and not _is_structured_envelope(result)
            ):
                # FastMCP RETURNS an isError result echoing "Unknown tool: '<name>'" on
                # the return path; replace any non-structured isError frame.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            return result

        lowlevel.add_request_handler("tools/call", call_tool.params_type, wrapped_call_tool)

    for method, message, kind in (
        ("resources/read", _UNKNOWN_RESOURCE_MESSAGE, "resource"),
        ("prompts/get", _UNKNOWN_PROMPT_MESSAGE, "prompt"),
    ):
        entry = lowlevel.get_request_handler(method)
        if entry is None:
            continue

        async def wrapped(
            ctx: Any,
            params: Any,
            *,
            _orig: Any = entry.handler,
            _message: str = message,
            _kind: str = kind,
        ) -> Any:
            try:
                return await _orig(ctx, params)
            except Exception as exc:
                # Re-raise with a FIXED, input-free message so no requested name/URI
                # (or its code points) reaches the JSON-RPC error frame. Log the
                # exception CLASS only (never the caller-controlled value).
                # MCPError keeps -32602 (SDK 2 sends plain exceptions as code 0).
                logger.warning("mcp_protocol_error kind=%s type=%s", _kind, type(exc).__name__)
                raise MCPError(code=INVALID_PARAMS, message=_message) from None

        lowlevel.add_request_handler(method, entry.params_type, wrapped)
