"""Tests for the MCP envelope boundary (panelapp_link.mcp.envelope)."""

from __future__ import annotations

from typing import Any

import pytest

from panelapp_link.exceptions import (
    DownloadError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
)
from panelapp_link.mcp.envelope import (
    McpErrorContext,
    McpToolError,
    run_mcp_tool,
    validation_error_envelope,
)


def _raiser(exc: Exception):
    async def body() -> dict[str, Any]:
        raise exc

    return body


class TestHappyPath:
    async def test_injects_success_and_meta(self) -> None:
        async def body() -> dict[str, Any]:
            return {"value": 42}

        out = await run_mcp_tool("t", body)
        assert out["success"] is True
        assert out["value"] == 42
        assert "_meta" in out
        assert out["_meta"]["recommended_citation"]
        assert out["_meta"]["unsafe_for_clinical_use"] is True

    async def test_meta_has_request_id_and_timing(self) -> None:
        async def body() -> dict[str, Any]:
            return {}

        out = await run_mcp_tool("t", body)
        assert isinstance(out["_meta"]["request_id"], str)
        assert len(out["_meta"]["request_id"]) == 12
        assert isinstance(out["_meta"]["elapsed_ms"], (int, float))
        assert out["_meta"]["elapsed_ms"] >= 0

    async def test_preserves_existing_meta(self) -> None:
        async def body() -> dict[str, Any]:
            return {"_meta": {"next_commands": [{"tool": "x", "arguments": {}}]}}

        out = await run_mcp_tool("t", body)
        assert out["_meta"]["next_commands"]
        assert out["_meta"]["unsafe_for_clinical_use"] is True

    async def test_does_not_overwrite_explicit_success(self) -> None:
        async def body() -> dict[str, Any]:
            return {"success": False, "custom": 1}

        out = await run_mcp_tool("t", body)
        assert out["success"] is False


class TestErrorClassification:
    @pytest.mark.parametrize(
        ("exc", "code", "retryable", "recovery"),
        [
            (NotFoundError("nope"), "not_found", False, "switch_tool"),
            (RateLimitError("limit"), "rate_limited", True, "retry_backoff"),
            (DownloadError("net"), "upstream_unavailable", True, "retry_backoff"),
            (RuntimeError("boom"), "internal_error", False, "retry_backoff"),
        ],
    )
    async def test_codes(self, exc: Exception, code: str, retryable: bool, recovery: str) -> None:
        out = await run_mcp_tool("t", _raiser(exc), context=McpErrorContext("t"))
        assert out["success"] is False
        assert out["error_code"] == code
        assert out["retryable"] is retryable
        assert out["recovery_action"] == recovery
        assert out["_meta"]["tool"] == "t"

    async def test_invalid_input_with_field_errors(self) -> None:
        out = await run_mcp_tool("t", _raiser(InvalidInputError("bad", field="query")))
        assert out["error_code"] == "invalid_input"
        assert out["field_errors"] == [{"field": "query", "reason": "bad"}]
        assert "`query`" in out["message"]

    async def test_invalid_input_without_field(self) -> None:
        out = await run_mcp_tool("t", _raiser(InvalidInputError("bad")))
        assert out["error_code"] == "invalid_input"
        assert out["message"] == "bad"
        assert "field_errors" not in out

    async def test_error_meta_has_request_id_and_timing(self) -> None:
        out = await run_mcp_tool("t", _raiser(NotFoundError("x")))
        assert "request_id" in out["_meta"]
        assert isinstance(out["_meta"]["elapsed_ms"], (int, float))

    async def test_pydantic_validation_error(self) -> None:
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        async def body() -> dict[str, Any]:
            M(x="not-an-int")  # type: ignore[arg-type]
            return {}

        out = await run_mcp_tool("t", body)
        assert out["error_code"] == "invalid_input"
        assert out["retryable"] is False
        assert out["field_errors"]


class TestMcpToolError:
    async def test_passes_custom_error_code(self) -> None:
        out = await run_mcp_tool(
            "t", _raiser(McpToolError(error_code="custom_code", message="msg"))
        )
        assert out["error_code"] == "custom_code"
        assert out["message"] == "msg"
        assert out["retryable"] is False

    async def test_retryable_codes(self) -> None:
        out = await run_mcp_tool("t", _raiser(McpToolError(error_code="rate_limited", message="m")))
        assert out["retryable"] is True
        out2 = await run_mcp_tool(
            "t", _raiser(McpToolError(error_code="upstream_unavailable", message="m"))
        )
        assert out2["retryable"] is True


class TestCitationByMode:
    @pytest.mark.parametrize("mode", ["minimal", "compact", "standard"])
    async def test_short_modes_use_citation_ref(self, mode: str) -> None:
        async def body() -> dict[str, Any]:
            return {}

        out = await run_mcp_tool("t", body, response_mode=mode)
        assert out["_meta"]["citation_ref"] == "panelapp://citation"
        assert out["_meta"]["citation_short"]
        assert "recommended_citation" not in out["_meta"]
        assert "data_license" not in out["_meta"]
        assert out["_meta"]["response_mode"] == mode

    async def test_full_uses_full_citation(self) -> None:
        async def body() -> dict[str, Any]:
            return {}

        out = await run_mcp_tool("t", body, response_mode="full")
        assert out["_meta"]["recommended_citation"]
        assert "PanelApp Australia" in out["_meta"]["recommended_citation"]
        assert out["_meta"]["data_license"]
        assert "citation_ref" not in out["_meta"]

    async def test_error_envelope_citation_ref_only(self) -> None:
        out = await run_mcp_tool("t", _raiser(NotFoundError("x")))
        meta = out["_meta"]
        assert meta["citation_ref"] == "panelapp://citation"
        assert "recommended_citation" not in meta
        assert "citation_short" not in meta
        assert "data_license" not in meta
        assert meta["unsafe_for_clinical_use"] is True


class TestErrorNextCommands:
    async def test_not_found_resolve_gene_recovery(self) -> None:
        out = await run_mcp_tool(
            "resolve_gene",
            _raiser(NotFoundError("nope")),
            context=McpErrorContext("resolve_gene", arguments={"query": "ZZZ"}),
        )
        assert out["_meta"]["next_commands"] == [
            {"tool": "search_panels", "arguments": {"query": "ZZZ"}}
        ]

    async def test_not_found_get_panel_omits_empty_search_recovery(self) -> None:
        # B-3: a bad panel_id has nothing to search; the slow-path
        # search_panels(query="") nudge is gone, so no next_commands are emitted.
        for tool in ("get_panel", "get_panel_genes"):
            out = await run_mcp_tool(
                tool,
                _raiser(NotFoundError("nope")),
                context=McpErrorContext(tool, arguments={"panel_id": 999, "region": "uk"}),
            )
            assert "next_commands" not in out["_meta"]
            assert out["recovery_action"] == "switch_tool"

    async def test_invalid_input_recovery_to_capabilities(self) -> None:
        out = await run_mcp_tool(
            "get_panel_genes",
            _raiser(InvalidInputError("bad", field="min_confidence")),
            context=McpErrorContext("get_panel_genes", arguments={}),
        )
        assert out["_meta"]["next_commands"][0]["tool"] == "get_server_capabilities"

    async def test_no_recovery_omits_next_commands(self) -> None:
        out = await run_mcp_tool("t", _raiser(RuntimeError("boom")))
        assert "next_commands" not in out["_meta"]


def _make_validation_error():
    from pydantic import BaseModel, ValidationError

    class _M(BaseModel):
        response_mode: str

    try:
        _M(response_mode=["not", "a", "str"])  # type: ignore[arg-type]
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


def test_validation_error_envelope_shape() -> None:
    env = validation_error_envelope(
        tool_name="get_panel_genes",
        arguments={"response_mode": "ultra"},
        exc=_make_validation_error(),
    )
    assert env["success"] is False
    assert env["error_code"] == "invalid_input"
    assert env["retryable"] is False
    assert env["recovery_action"] == "reformulate_input"
    assert env["field_errors"]
    assert env["_meta"]["tool"] == "get_panel_genes"
    assert env["_meta"]["next_commands"]
    assert isinstance(env["_meta"]["request_id"], str)
    assert "elapsed_ms" in env["_meta"]
