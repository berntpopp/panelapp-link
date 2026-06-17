from __future__ import annotations

from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp

_TRIMMED = ("search_panels", "get_panel", "get_panel_genes", "get_gene_panels")


async def test_descriptions_are_concise_but_keep_gotchas() -> None:
    async with Client(create_panelapp_mcp()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in _TRIMMED:
        assert len(tools[name].description) <= 320, name
    # Key gotchas preserved:
    assert "both" in tools["get_panel"].description.lower()
    assert "optional" in tools["get_gene_panels"].description.lower()
