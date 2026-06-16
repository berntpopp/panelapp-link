"""FastMCP middleware that converts pre-body argument-validation failures into
the structured ``invalid_input`` envelope.

FastMCP validates tool arguments (Pydantic ``TypeAdapter``) inside
``FunctionTool.run`` *before* the tool body runs, so an invalid ``response_mode``
or an unknown argument name raises ``pydantic.ValidationError`` where the body's
``run_mcp_tool`` boundary can never catch it. This middleware wraps the call,
catches that error, and returns a normal ``ToolResult`` whose structured content
is the same ``invalid_input`` envelope a domain error would produce -- so *every*
error the client sees is chainable, never a raw Pydantic/JSON-RPC dump.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from panelapp_link.mcp.envelope import validation_error_envelope


class InputValidationMiddleware(Middleware):
    """Re-wrap argument-validation errors as a structured ``invalid_input`` envelope."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ValidationError as exc:
            envelope = validation_error_envelope(
                tool_name=context.message.name,
                arguments=dict(context.message.arguments or {}),
                exc=exc,
            )
            return ToolResult(structured_content=envelope)
