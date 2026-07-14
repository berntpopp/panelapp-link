"""The README's `region` contract must match the live MCP tool schemas.

GeneFoundry README Standard v1: a documented fact that no machine checks will
rot. The README once claimed "Data tools take `region` (... default `both`)" —
false for `get_panel` / `get_panel_genes` (region is **required**, `both` is
rejected) and for `compare_panels` (no top-level `region` at all), so a reader
following the README omitted `region` and took a schema-validation error.

The oracle is the real server built by ``create_panelapp_mcp()``: this test
derives the three region classes from ``list_tools()`` and asserts the README
names exactly the right tools in each of its three claims.
"""

from __future__ import annotations

import re
from pathlib import Path

from panelapp_link.mcp.facade import create_panelapp_mcp

README = Path(__file__).resolve().parents[2] / "README.md"

# Sentences in the README paragraph that carries the region contract. Each claim
# lives in its own sentence so it can be checked against the schema on its own.
_DEFAULT_BOTH_CLAIM = "defaults to `both`"
_REQUIRED_CLAIM = "require"
_NO_REGION_CLAIM = "no top-level `region`"


async def _region_classes() -> tuple[set[str], set[str], set[str], set[str]]:
    """(all tools, region-defaults-to-both, region-required, data tools w/o region)."""
    mcp = create_panelapp_mcp()
    every: set[str] = set()
    default_both: set[str] = set()
    required: set[str] = set()
    no_region: set[str] = set()
    for tool in await mcp.list_tools():
        schema = tool.parameters or {}
        props = schema.get("properties", {})
        every.add(tool.name)
        region = props.get("region")
        if region is None:
            # Only argument-taking data tools are in scope; the discovery tools
            # (no arguments at all) are not part of the region contract.
            if props:
                no_region.add(tool.name)
            continue
        if "region" in schema.get("required", []):
            required.add(tool.name)
        elif region.get("default") == "both":
            default_both.add(tool.name)
    return every, default_both, required, no_region


def _region_paragraph() -> list[str]:
    """Sentences of the README paragraph stating the region contract."""
    paragraphs = README.read_text(encoding="utf-8").split("\n\n")
    hits = [p for p in paragraphs if _DEFAULT_BOTH_CLAIM in p]
    assert len(hits) == 1, (
        f"expected exactly one README paragraph containing {_DEFAULT_BOTH_CLAIM!r}, "
        f"found {len(hits)}"
    )
    flat = " ".join(hits[0].split())
    return [s.strip() for s in re.split(r"(?<=\.)\s+", flat) if s.strip()]


def _tools_named(sentence: str, every: set[str]) -> set[str]:
    """Tool names cited in backticks in one sentence (`get_panel` != `get_panel_genes`)."""
    return {name for name in every if f"`{name}`" in sentence}


def _sentence(sentences: list[str], claim: str) -> str:
    hits = [s for s in sentences if claim in s]
    assert len(hits) == 1, (
        f"expected exactly one sentence containing {claim!r} in the README's region "
        f"paragraph, found {len(hits)}: {hits}"
    )
    return hits[0]


async def test_readme_region_contract_matches_tool_schemas() -> None:
    """Each README region claim must name exactly the tools the schema puts in that class."""
    every, default_both, required, no_region = await _region_classes()
    assert default_both and required and no_region, (
        "the live schema no longer has all three region classes; the README wording "
        f"needs a rethink (default_both={default_both}, required={required}, "
        f"no_region={no_region})"
    )

    sentences = _region_paragraph()

    assert _tools_named(_sentence(sentences, _DEFAULT_BOTH_CLAIM), every) == default_both, (
        "the README's `region` defaults-to-`both` sentence must name exactly the tools "
        f"whose schema defaults region to 'both': {sorted(default_both)}"
    )
    assert _tools_named(_sentence(sentences, _REQUIRED_CLAIM), every) == required, (
        "the README's required-region sentence must name exactly the tools whose schema "
        f"lists 'region' as required: {sorted(required)}"
    )
    assert _tools_named(_sentence(sentences, _NO_REGION_CLAIM), every) == no_region, (
        "the README must name exactly the data tools with no top-level `region` "
        f"argument: {sorted(no_region)}"
    )
