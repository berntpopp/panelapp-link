"""Tests for next_commands builders (panelapp_link.mcp.next_commands)."""

from __future__ import annotations

import pytest

from panelapp_link.mcp import next_commands as nc


class TestCmd:
    def test_shape(self) -> None:
        out = nc.cmd("search_panels", query="cancer")
        assert out == {"tool": "search_panels", "arguments": {"query": "cancer"}}

    def test_keys(self) -> None:
        out = nc.cmd("get_panel", panel_id=3, region="uk")
        assert set(out.keys()) == {"tool", "arguments"}
        assert out["arguments"] == {"panel_id": 3, "region": "uk"}


class TestAfterGetGenePanels:
    def test_emits_get_panel_per_distinct_hit(self) -> None:
        panels = [
            {"region": "uk", "panel_id": 3, "panel_name": "A"},
            {"region": "australia", "panel_id": 137, "panel_name": "B"},
        ]
        out = nc.after_get_gene_panels(panels)
        assert out[0] == {"tool": "get_panel", "arguments": {"panel_id": 3, "region": "uk"}}
        assert out[1] == {
            "tool": "get_panel",
            "arguments": {"panel_id": 137, "region": "australia"},
        }

    def test_dedupes_and_skips_invalid(self) -> None:
        panels = [
            {"region": "uk", "panel_id": 3},
            {"region": "uk", "panel_id": 3},  # duplicate
            {"region": "both", "panel_id": 9},  # non-concrete region -> skipped
            {"region": "australia", "panel_id": None},  # no id -> skipped
        ]
        out = nc.after_get_gene_panels(panels)
        assert out == [{"tool": "get_panel", "arguments": {"panel_id": 3, "region": "uk"}}]

    def test_capped_at_five(self) -> None:
        panels = [{"region": "uk", "panel_id": i} for i in range(10)]
        assert len(nc.after_get_gene_panels(panels)) <= nc._MAX_NEXT_COMMANDS

    def test_empty(self) -> None:
        assert nc.after_get_gene_panels([]) == []


class TestAfterSearchPanels:
    def test_emits_get_panel_then_genes(self) -> None:
        panels = [{"id": 3, "region": "uk", "name": "X"}]
        out = nc.after_search_panels(panels)
        assert out[0] == {"tool": "get_panel", "arguments": {"panel_id": 3, "region": "uk"}}
        assert out[1] == {
            "tool": "get_panel_genes",
            "arguments": {"panel_id": 3, "region": "uk"},
        }

    def test_accepts_panel_id_key(self) -> None:
        panels = [{"panel_id": 285, "region": "australia", "name": "Y"}]
        out = nc.after_search_panels(panels)
        assert out[0]["arguments"] == {"panel_id": 285, "region": "australia"}

    def test_skips_panels_without_concrete_region(self) -> None:
        panels = [{"id": 3, "region": "both", "name": "X"}, {"id": 4, "name": "Z"}]
        assert nc.after_search_panels(panels) == []

    def test_empty(self) -> None:
        assert nc.after_search_panels([]) == []

    def test_capped_at_five(self) -> None:
        panels = [{"id": i, "region": "uk", "name": str(i)} for i in range(10)]
        out = nc.after_search_panels(panels)
        assert len(out) <= nc._MAX_NEXT_COMMANDS
        assert all(set(c.keys()) == {"tool", "arguments"} for c in out)


class TestAfterResolveGene:
    def test_prefers_hgnc_id(self) -> None:
        out = nc.after_resolve_gene({"gene_symbol": "BRCA1", "hgnc_id": "HGNC:1100"})
        assert out == [{"tool": "get_gene_panels", "arguments": {"hgnc_id": "HGNC:1100"}}]

    def test_falls_back_to_symbol(self) -> None:
        out = nc.after_resolve_gene({"gene_symbol": "BRCA1"})
        assert out == [{"tool": "get_gene_panels", "arguments": {"gene_symbol": "BRCA1"}}]

    def test_empty_without_identity(self) -> None:
        assert nc.after_resolve_gene({}) == []


class TestAfterGetPanel:
    def test_emits_get_panel_genes(self) -> None:
        out = nc.after_get_panel("uk", 3)
        assert out == [{"tool": "get_panel_genes", "arguments": {"panel_id": 3, "region": "uk"}}]


class TestRecoveryCommands:
    def test_not_found_get_panel(self) -> None:
        out = nc.recovery_commands(
            "get_panel", "not_found", {"panel_id": 999, "region": "uk"}, None
        )
        assert out == [{"tool": "search_panels", "arguments": {"query": ""}}]

    def test_not_found_resolve_gene(self) -> None:
        out = nc.recovery_commands("resolve_gene", "not_found", {"query": "ZZZ"}, None)
        assert out == [{"tool": "search_panels", "arguments": {"query": "ZZZ"}}]

    def test_not_found_get_gene_panels(self) -> None:
        out = nc.recovery_commands("get_gene_panels", "not_found", {"gene_symbol": "ZZZ"}, None)
        assert out == [{"tool": "resolve_gene", "arguments": {"query": "ZZZ"}}]

    def test_invalid_input_points_to_capabilities(self) -> None:
        out = nc.recovery_commands("get_panel_genes", "invalid_input", {}, "min_confidence")
        assert out == [{"tool": "get_server_capabilities", "arguments": {}}]

    def test_invalid_input_no_field_still_chainable(self) -> None:
        out = nc.recovery_commands("search_panels", "invalid_input", {}, None)
        assert out == [{"tool": "get_server_capabilities", "arguments": {}}]

    def test_unknown_returns_empty(self) -> None:
        assert nc.recovery_commands("search_panels", "internal_error", {}, None) == []

    @pytest.mark.parametrize(
        "tool, error_code, args, field",
        [
            ("get_panel", "not_found", {"panel_id": 1, "region": "uk"}, None),
            ("resolve_gene", "not_found", {"query": "x"}, None),
            ("get_gene_panels", "not_found", {"hgnc_id": "HGNC:1"}, None),
            ("search_panels", "invalid_input", {}, "query"),
        ],
    )
    def test_all_capped_at_five(
        self, tool: str, error_code: str, args: dict, field: str | None
    ) -> None:
        out = nc.recovery_commands(tool, error_code, args, field)
        assert len(out) <= nc._MAX_NEXT_COMMANDS
