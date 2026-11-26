"""Response-Envelope Standard v1.2: every data tool's success validates against the schema.

The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0
(https://raw.githubusercontent.com/berntpopp/genefoundry-envelope/v1.2.0/envelope-v1.2.schema.json).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing
from panelapp_link.services.panelapp_service import PanelAppService

SCHEMA_PATH = Path(__file__).with_name("envelope-v1.2.schema.json")
SCHEMA_SHA256 = "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

# One successful call per data tool. get_server_capabilities and get_panelapp_diagnostics
# are ops/meta tools and outside the v1.2 rule.
CALLS: dict[str, dict[str, Any]] = {
    "search_panels": {"query": "", "region": "both"},
    "get_panel": {"panel_id": 285, "region": "uk"},
    "get_panel_genes": {"panel_id": 285, "region": "uk"},
    "get_gene_panels": {"gene_symbol": "AAAS"},
    "resolve_gene": {"query": "AAAS"},
    "compare_panels": {
        "panels": [{"panel_id": 1207, "region": "uk"}, {"panel_id": 285, "region": "uk"}]
    },
    "get_panels_for_genes": {"gene_symbols": ["AAAS"], "region": "uk"},
}
OPS_TOOLS = {"get_server_capabilities", "get_panelapp_diagnostics"}


@pytest.fixture
async def client(live_service: PanelAppService) -> AsyncIterator[Client]:
    set_service_for_testing(live_service)
    try:
        async with Client(create_panelapp_mcp()) as mcp_client:
            yield mcp_client
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    assert sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


async def test_every_data_tool_is_covered(client: Client) -> None:
    registered = {tool.name for tool in await client.list_tools()}
    assert registered - OPS_TOOLS == set(CALLS)


@pytest.mark.parametrize("tool", sorted(CALLS))
async def test_success_validates_and_carries_provenance(client: Client, tool: str) -> None:
    envelope = (await client.call_tool(tool, CALLS[tool])).structured_content
    assert envelope["success"] is True
    jsonschema.validate(envelope, SCHEMA)
    meta = envelope["_meta"]
    assert meta["source"] == "panelapp"
    assert meta["data_version"] == "live"
    assert meta["retrieved_at"].endswith("Z")


async def test_cache_hit_reports_the_first_fetch_time(client: Client) -> None:
    args = {"panel_id": 285, "region": "uk"}
    first = (await client.call_tool("get_panel", args)).structured_content["_meta"]
    second = (await client.call_tool("get_panel", args)).structured_content["_meta"]
    assert second["cache"] == "hit"
    assert second["retrieved_at"] == first["retrieved_at"]


@pytest.mark.parametrize("tool", sorted(OPS_TOOLS))
async def test_ops_tools_keep_their_shape(client: Client, tool: str) -> None:
    envelope = (await client.call_tool(tool, {})).structured_content
    assert "result" not in envelope and "retrieved_at" not in envelope["_meta"]


async def test_error_envelope_validates_too(client: Client) -> None:
    result = await client.call_tool(
        "get_panel", {"panel_id": 999999, "region": "uk"}, raise_on_error=False
    )
    assert result.structured_content["success"] is False
    jsonschema.validate(result.structured_content, SCHEMA)
