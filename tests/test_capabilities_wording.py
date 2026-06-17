"""L2: capabilities/tool prose must name the real field, not 'strongest confidence'."""

from __future__ import annotations

import json

from fastmcp import Client

from panelapp_link.mcp.capabilities import build_capabilities
from panelapp_link.mcp.facade import create_panelapp_mcp


def test_capabilities_has_no_strongest_confidence_phrase() -> None:
    blob = json.dumps(build_capabilities()).lower()
    assert "strongest confidence" not in blob
    assert "strongest_confidence" not in blob


async def test_resolve_gene_description_names_field() -> None:
    async with Client(create_panelapp_mcp()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    desc = tools["resolve_gene"].description.lower()
    assert "strongest confidence" not in desc
    assert "max_confidence_label" in desc
