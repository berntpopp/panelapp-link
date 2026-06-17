from __future__ import annotations

from panelapp_link.mcp import next_commands as nc


def test_after_compare_panels_emits_panel_genes_per_ref() -> None:
    cmds = nc.after_compare_panels(
        [{"panel_id": 1, "region": "uk"}, {"panel_id": 2, "region": "australia"}]
    )
    assert cmds[0] == {"tool": "get_panel_genes", "arguments": {"panel_id": 1, "region": "uk"}}
    assert {
        "tool": "get_panel_genes",
        "arguments": {"panel_id": 2, "region": "australia"},
    } in cmds
    assert len(cmds) <= 5


def test_after_panels_for_genes_emits_gene_panels_for_found() -> None:
    cmds = nc.after_panels_for_genes({"PKD1": {"panel_count": 19}, "PKD2": {"panel_count": 3}})
    assert {"tool": "get_gene_panels", "arguments": {"gene_symbol": "PKD1"}} in cmds
    assert len(cmds) <= 5
