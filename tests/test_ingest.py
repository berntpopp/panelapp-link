"""Tests for the PanelApp ingest builder + crawler over committed fixtures."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from panelapp_link.api.client import PanelAppRestClient
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.ingest.builder import build_database, refresh
from panelapp_link.ingest.downloader import crawl_all, crawl_region
from tests.conftest import build_crawled_from_fixtures, load_fixture


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def test_build_produces_panel_counts(built_db: Path) -> None:
    """The build inserts UK and AU panels and records both counts in meta."""
    conn = _connect(built_db)
    try:
        uk = conn.execute("SELECT COUNT(*) FROM panel WHERE region = 'uk'").fetchone()[0]
        au = conn.execute("SELECT COUNT(*) FROM panel WHERE region = 'australia'").fetchone()[0]
        meta = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    finally:
        conn.close()
    # UK: 3 list panels (1207, 1141, 399) union detail ids (1207, 285) == 4.
    assert uk == 4
    # AU: 3 list panels (3149, 221, 3302).
    assert au == 3
    assert meta["uk_panel_count"] == 4
    assert meta["au_panel_count"] == 3


def test_entity_type_split_present(built_db: Path) -> None:
    """Panel 285 explodes into gene, region, and str entities."""
    conn = _connect(built_db)
    try:
        rows = conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM entity "
            "WHERE region = 'uk' AND panel_id = 285 GROUP BY entity_type"
        ).fetchall()
    finally:
        conn.close()
    counts = {r["entity_type"]: r["n"] for r in rows}
    assert counts.get("gene", 0) >= 1
    assert counts.get("region", 0) >= 1
    assert counts.get("str", 0) >= 1


def test_confidence_labels_and_ranks(built_db: Path) -> None:
    """confidence_level is mapped to label + rank; an amber STR is present."""
    conn = _connect(built_db)
    try:
        green = conn.execute(
            "SELECT confidence_label, confidence_rank FROM entity "
            "WHERE region = 'uk' AND panel_id = 285 AND entity_name = 'AAAS'"
        ).fetchone()
        amber = conn.execute(
            "SELECT confidence_label, confidence_rank FROM entity "
            "WHERE region = 'uk' AND panel_id = 285 AND entity_name = 'ATXN10_ATTCT'"
        ).fetchone()
    finally:
        conn.close()
    assert green["confidence_label"] == "green"
    assert green["confidence_rank"] == 3
    assert amber["confidence_label"] == "amber"
    assert amber["confidence_rank"] == 2


def test_region_and_str_extras_packed(built_db: Path) -> None:
    """Region/STR-specific fields are packed into extra_json; omim into omim_json."""
    conn = _connect(built_db)
    try:
        region = conn.execute(
            "SELECT extra_json FROM entity "
            "WHERE region = 'uk' AND panel_id = 285 AND entity_name = 'ISCA-37390-Loss'"
        ).fetchone()
        str_row = conn.execute(
            "SELECT extra_json FROM entity "
            "WHERE region = 'uk' AND panel_id = 285 AND entity_name = 'DMPK_CTG'"
        ).fetchone()
        gene = conn.execute(
            "SELECT omim_json FROM entity "
            "WHERE region = 'uk' AND panel_id = 1207 AND entity_name = 'HMBS'"
        ).fetchone()
    finally:
        conn.close()
    region_extra = json.loads(region["extra_json"])
    assert region_extra["verbose_name"].startswith("5p15")
    assert region_extra["type_of_variants"] == "cnv_loss"
    str_extra = json.loads(str_row["extra_json"])
    assert str_extra["repeated_sequence"] == "CTG"
    assert str_extra["normal_repeats"] == 35
    assert json.loads(gene["omim_json"]) == ["609806"]


def test_signed_off_unmatched_leaves_null(built_db: Path) -> None:
    """Signed-off ids with no matching panel row leave the columns NULL (no crash).

    The committed UK signed-off page covers panel ids 3, 9, 13, none of which are
    among the listed/detailed panels, so the merge produces no populated rows.
    """
    conn = _connect(built_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM panel WHERE signed_off_date IS NOT NULL LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_signed_off_merges_when_panel_present(tmp_path: Path) -> None:
    """When a listed panel id matches the signed-off map, the row is merged."""
    crawled = build_crawled_from_fixtures()
    # Inject a signed-off entry for a UK panel that IS listed/detailed (1207).
    crawled["uk"]["signed_off"][1207] = {"version": "2.1", "signed_off": "2026-05-06"}
    config = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="panelapp.sqlite")
    build_database(config, crawled)
    conn = _connect(config.db_path)
    try:
        row = conn.execute(
            "SELECT signed_off_version, signed_off_date FROM panel "
            "WHERE region = 'uk' AND panel_id = 1207"
        ).fetchone()
    finally:
        conn.close()
    assert row["signed_off_version"] == "2.1"
    assert row["signed_off_date"] == "2026-05-06"


def test_gene_rollup_across_regions(built_db: Path) -> None:
    """The gene table rolls up one row per symbol with panel_count and regions."""
    conn = _connect(built_db)
    try:
        hmbs = conn.execute("SELECT * FROM gene WHERE gene_symbol_upper = 'HMBS'").fetchone()
        atf6 = conn.execute("SELECT * FROM gene WHERE gene_symbol_upper = 'ATF6'").fetchone()
        total = conn.execute("SELECT COUNT(*) FROM gene").fetchone()[0]
    finally:
        conn.close()
    assert hmbs["regions_json"] == json.dumps(["uk"])
    assert hmbs["panel_count"] == 1
    assert hmbs["max_confidence_label"] == "green"
    # ATF6 only appears on the AU panel.
    assert json.loads(atf6["regions_json"]) == ["australia"]
    assert total > 0


def test_meta_panel_versions_json(built_db: Path) -> None:
    """meta.panel_versions_json captures {region:{panel_id:version}}."""
    conn = _connect(built_db)
    try:
        meta = conn.execute("SELECT panel_versions_json FROM meta WHERE id = 1").fetchone()
    finally:
        conn.close()
    versions = json.loads(meta["panel_versions_json"])
    assert versions["uk"]["1207"] == "2.1"
    assert versions["uk"]["285"] == "10.18"
    assert versions["australia"]["3149"] == "2.0"


UK_BASE = "https://uk.example.org/api/v1"
AU_BASE = "https://au.example.org/api/v1"


def _crawl_config(tmp_path: Path) -> PanelAppDataConfigModel:
    return PanelAppDataConfigModel(
        data_dir=tmp_path,
        db_filename="panelapp.sqlite",
        uk_api_url=UK_BASE,
        au_api_url=AU_BASE,
        max_retries=1,
        max_concurrency=4,
    )


def _mock_region(base: str, panels_fixture: str, detail_fixture: str, panel_id: int) -> None:
    """Register respx routes for one region's list/signed-off/detail endpoints."""
    respx.get(f"{base}/panels/").mock(
        return_value=httpx.Response(200, json=load_fixture(panels_fixture))
    )
    respx.get(f"{base}/panels/signedoff/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    respx.get(f"{base}/panels/{panel_id}/").mock(
        return_value=httpx.Response(200, json=load_fixture(detail_fixture))
    )


@pytest.mark.asyncio
@respx.mock
async def test_crawl_region_collects_panels_and_details() -> None:
    """crawl_region returns panels, a signed-off map, and per-panel details."""
    respx.get(f"{UK_BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": 1207, "name": "Acute", "version": "2.1"}],
            },
        )
    )
    respx.get(f"{UK_BASE}/panels/signedoff/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": 3, "version": "4.0", "signed_off": "2023-03-22"}],
            },
        )
    )
    respx.get(f"{UK_BASE}/panels/1207/").mock(
        return_value=httpx.Response(200, json=load_fixture("uk_panel_1207.json"))
    )
    client = PanelAppRestClient(PanelAppDataConfigModel(uk_api_url=UK_BASE, max_retries=1))
    try:
        crawled = await crawl_region(client, "uk", UK_BASE)
    finally:
        await client.aclose()
    assert [p["id"] for p in crawled["panels"]] == [1207]
    assert crawled["signed_off"][3] == {"version": "4.0", "signed_off": "2023-03-22"}
    assert crawled["details"][1207]["name"] == "Acute intermittent porphyria"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_builds_then_skips_when_unchanged(tmp_path: Path) -> None:
    """refresh builds first time, then returns existing meta when nothing changed."""
    config = _crawl_config(tmp_path)
    # Only fetch detail for the single listed panel per region; stub list payloads
    # with one panel each so crawl_all fetches exactly one detail per region.
    respx.get(f"{UK_BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={"count": 1, "next": None, "results": [{"id": 1207, "version": "2.1"}]},
        )
    )
    respx.get(f"{UK_BASE}/panels/signedoff/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    respx.get(f"{UK_BASE}/panels/1207/").mock(
        return_value=httpx.Response(200, json=load_fixture("uk_panel_1207.json"))
    )
    respx.get(f"{AU_BASE}/panels/").mock(
        return_value=httpx.Response(
            200,
            json={"count": 1, "next": None, "results": [{"id": 3149, "version": "2.0"}]},
        )
    )
    respx.get(f"{AU_BASE}/panels/signedoff/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    respx.get(f"{AU_BASE}/panels/3149/").mock(
        return_value=httpx.Response(200, json=load_fixture("au_panel_3149.json"))
    )

    first = await refresh(config, force=True)
    assert first.uk_panel_count == 1
    assert first.au_panel_count == 1

    # Second refresh: versions unchanged -> existing meta returned, no rebuild.
    second = await refresh(config, force=False)
    assert second.build_utc == first.build_utc
    assert second.uk_panel_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_crawl_all_covers_both_regions(tmp_path: Path) -> None:
    """crawl_all returns a per-region mapping for both UK and Australia."""
    _mock_region(UK_BASE, "uk_panels_page1.json", "uk_panel_1207.json", 1207)
    # uk_panels_page1 lists ids 1207, 1141, 399; stub the others' detail too.
    respx.get(f"{UK_BASE}/panels/1141/").mock(
        return_value=httpx.Response(200, json={"id": 1141, "name": "x", "version": "2.10"})
    )
    respx.get(f"{UK_BASE}/panels/399/").mock(
        return_value=httpx.Response(200, json={"id": 399, "name": "y", "version": "0.116"})
    )
    _mock_region(AU_BASE, "au_panels_page1.json", "au_panel_3149.json", 3149)
    respx.get(f"{AU_BASE}/panels/221/").mock(
        return_value=httpx.Response(200, json={"id": 221, "name": "a", "version": "3.0"})
    )
    respx.get(f"{AU_BASE}/panels/3302/").mock(
        return_value=httpx.Response(200, json={"id": 3302, "name": "b", "version": "1.0"})
    )
    config = _crawl_config(tmp_path)
    crawled = await crawl_all(config)
    assert set(crawled) == {"uk", "australia"}
    assert len(crawled["uk"]["details"]) == 3
    assert len(crawled["australia"]["details"]) == 3
