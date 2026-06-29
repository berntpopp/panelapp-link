"""PanelApp-Link: MCP/API server for PanelApp (UK + Australia) gene-panel data."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version declared in pyproject.toml. Keeps
    # __version__ (used by /health and structured logs) in lockstep with the
    # capabilities/diagnostics server_version (also read from package metadata).
    __version__ = version("panelapp-link")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.3.1"

__author__ = "PanelApp-Link Development Team"
