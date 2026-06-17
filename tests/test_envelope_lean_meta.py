"""WS-4: minimal mode drops heavy _meta keys; compact keeps them."""

from __future__ import annotations

from panelapp_link.mcp.envelope import run_mcp_tool


async def _body() -> dict:
    return {
        "ok": True,
        "_meta": {
            "next_commands": [{"tool": "a", "arguments": {}}, {"tool": "b", "arguments": {}}],
            "cache": "hit",
            "upstream_ms": 12.0,
            "upstream": {"uk": {"calls": 1, "ms": 12.0}},
        },
    }


async def test_minimal_meta_is_lean() -> None:
    out = await run_mcp_tool("search_panels", _body, response_mode="minimal")
    meta = out["_meta"]
    assert "upstream" not in meta
    assert "upstream_ms" not in meta
    assert "citation_short" not in meta
    assert len(meta["next_commands"]) == 1
    assert meta["request_id"]
    assert "elapsed_ms" in meta


async def test_compact_meta_keeps_breadcrumbs() -> None:
    out = await run_mcp_tool("search_panels", _body, response_mode="compact")
    meta = out["_meta"]
    assert meta["citation_short"]
    assert "upstream_ms" in meta
