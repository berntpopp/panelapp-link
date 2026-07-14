"""The README '## Tools' table must match the registered MCP tool surface.

GeneFoundry README Standard v1, Rule 6: the table is machine-verified, not
hand-maintained. Adding, renaming, or removing a tool without updating the README
fails CI here.

The live tool list is obtained exactly as ``tests/unit/test_tool_names.py`` does
it — build the real FastMCP server via ``create_panelapp_mcp()`` and enumerate
``list_tools()`` — so the two guards can never disagree about what "registered"
means.
"""

from __future__ import annotations

import re
from pathlib import Path

from panelapp_link.mcp.facade import create_panelapp_mcp

README = Path(__file__).resolve().parents[2] / "README.md"

# A table row: | `tool_name` | purpose |
_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _readme_tools() -> set[str]:
    """Tool names listed in the README's '## Tools' table."""
    lines = README.read_text(encoding="utf-8").splitlines()
    names: set[str] = set()
    in_section = False
    for line in lines:
        if line.startswith("## "):
            in_section = line[3:].strip() == "Tools"
            continue
        if not in_section:
            continue
        match = _ROW_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


async def test_readme_tools_table_matches_registered_tools() -> None:
    """The README table and the server's tool surface must be identical sets."""
    mcp = create_panelapp_mcp()
    registered = {tool.name for tool in await mcp.list_tools()}
    documented = _readme_tools()

    assert documented, "no tool rows parsed from the README '## Tools' table"
    assert documented == registered, (
        f"README '## Tools' table is out of sync with the registered tools.\n"
        f"Missing from README: {sorted(registered - documented)}\n"
        f"Not registered (stale README row): {sorted(documented - registered)}"
    )
