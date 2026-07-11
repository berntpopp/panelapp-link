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
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError as FastMCPResourceError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.resources.base import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    GetPromptRequest,
    ReadResourceRequest,
    ServerResult,
    TextContent,
)
from pydantic import ValidationError as PydanticValidationError

from panelapp_link.mcp.envelope import (
    arg_validation_failure_envelope,
    unknown_tool_envelope,
    validation_error_envelope,
)

logger = logging.getLogger(__name__)

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
                return ToolResult(structured_content=unknown_tool_envelope())
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            # Backstop for the raise path when no fastmcp context was available.
            logger.warning("mcp_unknown_tool")
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
# error frame. Resources are handled entirely by Layer 2 above (their handler wraps a
# NotFoundError into a fixed "Resource not found: <our URI-free message>"), so they are
# intentionally not re-wrapped here. All messages are fixed server-authored constants.


class _ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


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
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> ServerResult:
    """A fixed, name-free CallToolResult for an unknown/failed tool dispatch."""
    envelope = unknown_tool_envelope()
    return ServerResult(
        CallToolResult(
            content=[TextContent(type="text", text=json.dumps(envelope))],
            structuredContent=envelope,
            isError=True,
        )
    )


def install_protocol_error_handler(mcp: FastMCP) -> None:
    """Wrap the raw tool/prompt request handlers so a FastMCP-core not-found error can
    never reflect the caller-supplied name.

    Install AFTER all tools/resources are registered (so the handlers exist) and as the
    OUTERMOST wrapper on ``CallToolRequest`` / ``GetPromptRequest``.
    """
    handlers = mcp._mcp_server.request_handlers

    call_tool = handlers.get(CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> ServerResult:
            try:
                result = cast("ServerResult", await _orig(request))
            except Exception:
                # A registered tool never raises here (run_mcp_tool returns an
                # envelope); any exception is a dispatch-level failure whose message
                # would echo the caller name -- mask it.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            root = getattr(result, "root", None)
            if (
                isinstance(root, CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
            ):
                # FastMCP RETURNS an isError result echoing "Unknown tool: '<name>'" on
                # the return path; replace any non-structured isError frame.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            return result

        handlers[CallToolRequest] = wrapped_call_tool

    get_prompt = handlers.get(GetPromptRequest)
    if get_prompt is not None:

        async def wrapped_get_prompt(
            request: GetPromptRequest,
            *,
            _orig: Any = get_prompt,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception as exc:
                # Re-raise with a FIXED, input-free message so no requested prompt name
                # (or its code points) reaches the JSON-RPC error frame. Log the
                # exception CLASS only (never the caller-controlled value).
                logger.warning("mcp_protocol_error kind=prompt type=%s", type(exc).__name__)
                raise _ProtocolError(_UNKNOWN_PROMPT_MESSAGE) from None

        handlers[GetPromptRequest] = wrapped_get_prompt

    # ``ReadResourceRequest`` is intentionally NOT wrapped: Layer 2 already re-raises a
    # fixed URI-free NotFoundError/ResourceError, which the low-level read handler wraps
    # into a fixed "Resource not found: <our message>" -- no caller URI survives.
    _ = ReadResourceRequest  # documented: handled by Layer 2, not re-wrapped here.
