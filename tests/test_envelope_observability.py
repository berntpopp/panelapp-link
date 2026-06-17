"""Tests for observability wiring in the MCP envelope (run_mcp_tool).

The envelope is the single choke point for every tool call: it opens a telemetry
scope + trace span, records RED metrics, folds the per-call cache/upstream block
into ``_meta``, and trims next_commands in minimal mode to cut the per-call tax.
"""

from __future__ import annotations

from typing import Any

from panelapp_link.exceptions import NotFoundError
from panelapp_link.mcp.envelope import run_mcp_tool, validation_error_envelope
from panelapp_link.observability import telemetry as tel
from panelapp_link.observability.metrics import get_metrics, reset_metrics


async def test_meta_carries_cache_and_upstream_from_scope() -> None:
    reset_metrics()

    async def body() -> dict[str, Any]:
        # Simulate what the cache layer records during a cold fetch.
        tel.record_cache_miss()
        tel.record_upstream("uk", "panels", 120.0)
        return {"ok": True}

    out = await run_mcp_tool("search_panels", body, response_mode="compact")
    assert out["_meta"]["cache"] == "miss"
    assert out["_meta"]["upstream_ms"] == 120.0
    assert out["_meta"]["upstream"]["uk"]["calls"] == 1


async def test_success_records_red_metrics() -> None:
    reset_metrics()

    async def body() -> dict[str, Any]:
        return {}

    await run_mcp_tool("resolve_gene", body, response_mode="compact")
    snap = get_metrics().snapshot()
    assert snap["requests_total"] == 1
    assert snap["requests_by_tool"]["resolve_gene"] == 1
    assert snap["errors_total"] == 0
    assert "resolve_gene" in snap["tool_duration_ms"]


async def test_error_records_error_by_code() -> None:
    reset_metrics()

    async def body() -> dict[str, Any]:
        raise NotFoundError("nope")

    out = await run_mcp_tool("get_panel", body, response_mode="compact")
    assert out["success"] is False
    assert out["error_code"] == "not_found"
    snap = get_metrics().snapshot()
    assert snap["errors_by_code"]["not_found"] == 1
    assert snap["requests_total"] == 1


async def test_minimal_mode_trims_next_commands_to_one() -> None:
    reset_metrics()

    async def body() -> dict[str, Any]:
        return {
            "_meta": {
                "next_commands": [
                    {"tool": "get_panel", "arguments": {}},
                    {"tool": "get_panel_genes", "arguments": {}},
                    {"tool": "resolve_gene", "arguments": {}},
                ]
            }
        }

    minimal = await run_mcp_tool("search_panels", body, response_mode="minimal")
    assert len(minimal["_meta"]["next_commands"]) == 1


async def test_compact_mode_keeps_next_commands() -> None:
    reset_metrics()

    async def body() -> dict[str, Any]:
        return {
            "_meta": {
                "next_commands": [{"tool": "a", "arguments": {}}, {"tool": "b", "arguments": {}}]
            }
        }

    compact = await run_mcp_tool("search_panels", body, response_mode="compact")
    assert len(compact["_meta"]["next_commands"]) == 2


async def test_validation_error_envelope_records_metric() -> None:
    reset_metrics()
    from pydantic import TypeAdapter, ValidationError

    try:
        TypeAdapter(int).validate_python("not-an-int")
    except ValidationError as exc:
        validation_error_envelope(tool_name="search_panels", arguments={}, exc=exc)
    snap = get_metrics().snapshot()
    assert snap["errors_by_code"]["invalid_input"] == 1
