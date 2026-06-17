from __future__ import annotations

from panelapp_link.mcp.capabilities import build_capabilities


def test_capabilities_lists_nine_tools() -> None:
    caps = build_capabilities()
    assert "compare_panels" in caps["tools"]
    assert "get_panels_for_genes" in caps["tools"]
    assert len(caps["tools"]) == 9


def test_workflows_mention_new_tools() -> None:
    workflows = " ".join(build_capabilities()["recommended_workflows"])
    assert "compare_panels" in workflows
    assert "get_panels_for_genes" in workflows
