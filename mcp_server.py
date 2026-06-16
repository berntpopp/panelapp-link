"""PanelApp-Link stdio MCP entrypoint (W0 placeholder).

The real stdio entry (sets env defaults, runs the FastMCP server over stdio)
lands in the W9 integration barrier. This stub exists so the package builds,
the ``panelapp-link-mcp`` console script resolves, and force-include packaging
works during the W0 substrate phase. Replace it in W9.
"""

from __future__ import annotations


def main() -> None:
    """Entrypoint placeholder; real stdio MCP wiring lands in W9."""
    raise SystemExit(
        "panelapp-link-mcp stdio server is not implemented yet (W9 integration "
        "barrier). This is a W0 scaffold placeholder."
    )


if __name__ == "__main__":
    main()
