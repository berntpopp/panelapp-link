"""End-to-end data lifecycle: build -> repository -> query -> rebuild swap."""

from __future__ import annotations

from pathlib import Path

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.data.repository import PanelAppRepository
from panelapp_link.ingest.builder import build_database
from tests.conftest import build_crawled_from_fixtures


def test_build_then_query_then_close(tmp_path: Path) -> None:
    """A freshly built database is queryable through the repository."""
    config = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="panelapp.sqlite")
    meta = build_database(config, build_crawled_from_fixtures())
    assert meta.uk_panel_count == 4
    assert meta.au_panel_count == 3

    repo = PanelAppRepository(config.db_path)
    try:
        panel = repo.get_panel("uk", 285)
        assert panel is not None
        assert panel["name"] == "Intellectual disability"
        gene = repo.get_gene("HMBS")
        assert gene is not None
    finally:
        repo.close()


def test_rebuild_swaps_atomically(tmp_path: Path) -> None:
    """A second build replaces the database in place (atomic os.replace)."""
    config = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="panelapp.sqlite")
    build_database(config, build_crawled_from_fixtures())
    assert config.db_path.exists()

    # Mutate the crawl so the rebuilt DB differs, then rebuild.
    crawled = build_crawled_from_fixtures()
    crawled["australia"]["details"].pop(3149, None)
    crawled["australia"]["panels"] = [p for p in crawled["australia"]["panels"] if p["id"] != 3149]
    meta = build_database(config, crawled)

    # The temp file must be gone (renamed), and the new DB reflects the change.
    assert not config.db_path.with_suffix(".sqlite.tmp").exists()
    assert meta.au_panel_count == 2
    repo = PanelAppRepository(config.db_path)
    try:
        assert repo.get_panel("australia", 3149) is None
    finally:
        repo.close()
