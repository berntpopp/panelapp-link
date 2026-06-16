"""PanelApp-Link unified server entrypoint (W0 placeholder).

The real implementation (argparse transport switch -> UnifiedServerManager)
lands in the W9 integration barrier. This stub exists so the package builds,
the ``panelapp-link`` console script resolves, and force-include packaging
works during the W0 substrate phase. Replace it in W9.
"""

from __future__ import annotations


def main() -> None:
    """Entrypoint placeholder; real server wiring lands in W9."""
    raise SystemExit(
        "panelapp-link server is not implemented yet (W9 integration barrier). "
        "This is a W0 scaffold placeholder."
    )


if __name__ == "__main__":
    main()
