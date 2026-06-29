from __future__ import annotations

import pytest
from fastmcp import Client

from panelapp_link.config import settings
from panelapp_link.mcp.capabilities import server_version
from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing


def test_server_version_is_0_3_1() -> None:
    assert server_version() == "0.3.1"


@pytest.fixture
def mcp_client(live_service):
    set_service_for_testing(live_service)
    try:
        yield Client(create_panelapp_mcp())
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


async def test_get_panels_for_genes_honours_configured_cap(mcp_client, monkeypatch) -> None:
    # Tighten the cap to 1; the tool must truncate the second symbol server-side.
    monkeypatch.setattr(settings.data, "gene_batch_cap", 1)
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panels_for_genes",
            {"gene_symbols": ["AAAS", "HMBS"], "region": "uk"},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert body["truncated"]["requested"] == 2
    assert body["truncated"]["processed"] == 1
    assert len(body["genes"]) == 1
