"""Guard: pyproject -> installed metadata -> __version__ -> MCP serverInfo are one value.

Regression guard for the serverInfo.version leak: the MCP ``initialize`` response
must advertise the *package* version (from package metadata, single-sourced in
``pyproject.toml``), never the FastMCP framework version. The framework version
leaks when the ``FastMCP(...)`` constructor is created without a ``version=`` arg.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from panelapp_link import __version__
from panelapp_link.mcp.facade import create_panelapp_mcp

DIST = "panelapp-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    assert create_panelapp_mcp().version == __version__
