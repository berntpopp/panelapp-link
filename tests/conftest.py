"""Shared pytest fixtures for the PanelApp-Link test suite.

Provides committed JSON fixture loaders, a temp DB path, a ``built_db`` fixture
that builds a real SQLite database from the committed PanelApp fixtures via the
ingest builder, and a ``repository`` fixture over that database.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.ingest.builder import build_database

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


def _signed_off_map(name: str) -> dict[int, dict[str, Any]]:
    """Build a ``{panel_id: {version, signed_off}}`` map from a signed-off page."""
    page = load_fixture(name)
    out: dict[int, dict[str, Any]] = {}
    for row in page.get("results", []):
        out[int(row["id"])] = {
            "version": row.get("version"),
            "signed_off": row.get("signed_off"),
        }
    return out


def build_crawled_from_fixtures() -> dict[str, Any]:
    """Assemble a downloader-shaped crawl dict from the committed fixtures.

    UK: ``uk_panels_page1`` list + ``uk_signedoff_page1`` map + details for
    panels 1207 and 285 (285 carries regions + strs). AU: ``au_panels_page1``
    list + detail for panel 3149.
    """
    uk_panels = load_fixture("uk_panels_page1.json")["results"]
    uk_detail_1207 = load_fixture("uk_panel_1207.json")
    uk_detail_285 = load_fixture("uk_panel_285.json")
    au_panels = load_fixture("au_panels_page1.json")["results"]
    au_detail_3149 = load_fixture("au_panel_3149.json")
    return {
        "uk": {
            "panels": uk_panels,
            "signed_off": _signed_off_map("uk_signedoff_page1.json"),
            "details": {1207: uk_detail_1207, 285: uk_detail_285},
        },
        "australia": {
            "panels": au_panels,
            "signed_off": {},
            "details": {3149: au_detail_3149},
        },
    }


@pytest.fixture
def tmp_db_path() -> Iterator[Path]:
    """Yield a path for a temporary SQLite database in a throwaway directory."""
    with tempfile.TemporaryDirectory(prefix="panelapp-test-") as tmp:
        yield Path(tmp) / "panelapp.sqlite"


@pytest.fixture
def built_db(tmp_path: Path) -> Path:
    """Build a temporary PanelApp SQLite database from the committed fixtures."""
    config = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="panelapp.sqlite")
    build_database(config, build_crawled_from_fixtures())
    return config.db_path


@pytest.fixture
def repository(built_db: Path) -> Iterator[PanelAppRepository]:
    """Yield a read-only repository over the ``built_db`` fixture."""
    repo = PanelAppRepository(built_db)
    try:
        yield repo
    finally:
        repo.close()
