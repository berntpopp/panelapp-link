"""Shared pytest fixtures for the PanelApp-Link test suite.

W0 substrate provides the fixture *helpers* only: committed JSON fixture
loaders and a temp DB path. Later workstreams fill in the SQLite build (W3
builder), repository (W2), service (W4), and MCP client (W5/W7) fixtures that
mirror the gencc-link conftest pattern.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a committed JSON fixture by filename from ``tests/fixtures``.

    Args:
        name: Fixture filename, e.g. ``"uk_panels_page1.json"``.

    Returns:
        The parsed JSON object.
    """
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@pytest.fixture
def tmp_db_path() -> Iterator[Path]:
    """Yield a path for a temporary SQLite database in a throwaway directory.

    The W3 builder fills this DB; for now it is just an unused path inside a
    temp directory that is cleaned up after the test.
    """
    with tempfile.TemporaryDirectory(prefix="panelapp-test-") as tmp:
        yield Path(tmp) / "panelapp.sqlite"
