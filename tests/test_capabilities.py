"""Tests for the capabilities surface (panelapp_link.mcp.capabilities)."""

from __future__ import annotations

import re

from panelapp_link.mcp.capabilities import (
    TOOLS,
    build_capabilities,
    capabilities_version,
    register_capability_resources,
)

_EXPECTED_TOOLS = {
    "search_panels",
    "get_panel",
    "get_panel_genes",
    "get_gene_panels",
    "resolve_gene",
    "get_server_capabilities",
    "get_panelapp_diagnostics",
}


class TestBuildCapabilities:
    def test_seven_tools(self) -> None:
        caps = build_capabilities()
        assert len(caps["tools"]) == 7
        assert set(caps["tools"]) == _EXPECTED_TOOLS
        assert set(TOOLS) == _EXPECTED_TOOLS

    def test_server_identity(self) -> None:
        caps = build_capabilities()
        assert caps["server"] == "panelapp-link"
        assert caps["server_version"]
        assert caps["mcp_protocol_version"]

    def test_vocabulary(self) -> None:
        caps = build_capabilities()
        vocab = caps["vocabulary"]
        labels = {c["label"]: c["rank"] for c in vocab["confidence_labels"]}
        assert labels == {"green": 3, "amber": 2, "red": 1}
        assert "gene" in vocab["entity_types"]
        assert "region" in vocab["entity_types"]
        assert "str" in vocab["entity_types"]
        assert set(vocab["regions"]) >= {"uk", "australia", "both"}

    def test_response_modes(self) -> None:
        caps = build_capabilities()
        assert set(caps["response_modes"]) == {"minimal", "compact", "standard", "full"}

    def test_error_codes(self) -> None:
        caps = build_capabilities()
        codes = {e["code"]: e for e in caps["error_codes"]}
        for code in (
            "invalid_input",
            "not_found",
            "ambiguous_query",
            "data_unavailable",
            "upstream_unavailable",
            "rate_limited",
            "internal_error",
        ):
            assert code in codes
        assert codes["data_unavailable"]["operational_only"] is True
        assert codes["upstream_unavailable"]["operational_only"] is True
        assert codes["rate_limited"]["operational_only"] is True
        assert codes["invalid_input"]["operational_only"] is False
        assert codes["not_found"]["operational_only"] is False

    def test_resources_map(self) -> None:
        caps = build_capabilities()
        for uri in (
            "panelapp://capabilities",
            "panelapp://usage",
            "panelapp://reference",
            "panelapp://license",
            "panelapp://citation",
            "panelapp://research-use",
        ):
            assert uri in caps["resources"]

    def test_tool_defaults(self) -> None:
        caps = build_capabilities()
        assert caps["tool_defaults"]["search_panels"] == "compact"
        assert caps["tool_defaults"]["get_server_capabilities"] == "n/a"

    def test_capabilities_version_16_hex(self) -> None:
        caps = build_capabilities()
        version = caps["capabilities_version"]
        assert len(version) == 16
        assert re.fullmatch(r"[0-9a-f]{16}", version)


class TestCapabilitiesVersion:
    def test_stable_across_calls(self) -> None:
        assert capabilities_version() == capabilities_version()

    def test_matches_surface(self) -> None:
        assert capabilities_version() == build_capabilities()["capabilities_version"]

    def test_stable_vs_live_data(self) -> None:
        # The live data block lives outside the hashed static surface.
        assert build_capabilities()["capabilities_version"] == capabilities_version()


class TestDataBlock:
    def test_data_block_is_live(self) -> None:
        caps = build_capabilities()
        assert caps["data"]["mode"] == "live"
        assert "uk" in caps["data"]["sources"]
        assert "australia" in caps["data"]["sources"]
        assert "cache_ttl_seconds" in caps["data"]

    def test_data_block_outside_hash(self) -> None:
        # The live data block lives outside the hashed static surface.
        assert build_capabilities()["capabilities_version"] == capabilities_version()


class TestRegisterResources:
    async def test_registers_six_resources(self) -> None:
        from fastmcp import FastMCP

        mcp = FastMCP("test-panelapp")
        register_capability_resources(mcp)
        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        for uri in (
            "panelapp://capabilities",
            "panelapp://usage",
            "panelapp://reference",
            "panelapp://license",
            "panelapp://citation",
            "panelapp://research-use",
        ):
            assert uri in uris
        assert len(uris) >= 6

    async def test_resources_return_non_empty(self) -> None:
        from fastmcp import FastMCP

        mcp = FastMCP("test-panelapp-read")
        register_capability_resources(mcp)
        for uri in (
            "panelapp://usage",
            "panelapp://reference",
            "panelapp://license",
            "panelapp://citation",
            "panelapp://research-use",
        ):
            result = await mcp.read_resource(uri)
            text = result.contents[0].content
            assert isinstance(text, str)
            assert text.strip()
