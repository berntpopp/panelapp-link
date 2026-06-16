"""Tests for InputValidationMiddleware (panelapp_link.mcp.middleware).

The middleware wraps a tool call and converts a pre-body ``pydantic.ValidationError``
(argument validation, which the tool body can never catch) into the structured
``invalid_input`` envelope wrapped in a ToolResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastmcp.tools.tool import ToolResult
from pydantic import BaseModel, ValidationError

from panelapp_link.mcp.middleware import InputValidationMiddleware


@dataclass
class _Message:
    name: str
    arguments: dict[str, Any] | None


@dataclass
class _Context:
    message: _Message


def _make_validation_error() -> ValidationError:
    class _M(BaseModel):
        response_mode: str

    try:
        _M(response_mode=["not", "a", "str"])  # type: ignore[arg-type]
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


async def test_validation_error_becomes_invalid_input_envelope() -> None:
    mw = InputValidationMiddleware()
    ctx = _Context(_Message(name="get_panel_genes", arguments={"response_mode": ["bad"]}))

    async def call_next(_ctx: Any) -> ToolResult:
        raise _make_validation_error()

    result = await mw.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert isinstance(result, ToolResult)
    env = result.structured_content
    assert env is not None
    assert env["success"] is False
    assert env["error_code"] == "invalid_input"
    assert env["retryable"] is False
    assert env["field_errors"]
    assert env["_meta"]["tool"] == "get_panel_genes"


async def test_none_arguments_handled() -> None:
    # context.message.arguments may be None; the middleware must coerce to {}.
    mw = InputValidationMiddleware()
    ctx = _Context(_Message(name="search_panels", arguments=None))

    async def call_next(_ctx: Any) -> ToolResult:
        raise _make_validation_error()

    result = await mw.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert result.structured_content is not None
    assert result.structured_content["_meta"]["tool"] == "search_panels"


async def test_successful_call_passes_through_unchanged() -> None:
    mw = InputValidationMiddleware()
    ctx = _Context(_Message(name="search_panels", arguments={}))
    sentinel = ToolResult(structured_content={"success": True, "ok": 1})

    async def call_next(_ctx: Any) -> ToolResult:
        return sentinel

    result = await mw.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert result is sentinel


async def test_non_validation_error_propagates() -> None:
    # Only ValidationError is intercepted; other errors must bubble up.
    mw = InputValidationMiddleware()
    ctx = _Context(_Message(name="search_panels", arguments={}))

    async def call_next(_ctx: Any) -> ToolResult:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await mw.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
