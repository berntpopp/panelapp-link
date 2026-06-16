"""Atomic SQLite database builder for crawled PanelApp payloads.

``build_database`` is pure and synchronous: given an in-memory crawl dict (from
``panelapp_link.ingest.downloader``) it writes a fresh database to a temp file,
explodes panel entities (genes + regions + strs), rolls up a per-gene table,
populates the FTS index, records provenance, and atomically swaps the finished
file into place. ``refresh`` wraps a crawl + build under the build lock, with an
incremental fast path driven by the stored ``panel_versions_json``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from panelapp_link.constants import (
    CONFIDENCE_RANK,
    SCHEMA_VERSION,
    confidence_label,
)
from panelapp_link.data import load_schema_sql
from panelapp_link.exceptions import DataUnavailableError
from panelapp_link.ingest.downloader import crawl_all
from panelapp_link.ingest.lock import build_lock
from panelapp_link.models.records import BuildMeta

if TYPE_CHECKING:
    from panelapp_link.api.client import PanelAppRestClient
    from panelapp_link.config import PanelAppDataConfigModel

# Region keys, in deterministic order.
_REGIONS = ("uk", "australia")

# gene_data sub-fields packed into region/str ``extra_json``.
_REGION_EXTRA_FIELDS = (
    "chromosome",
    "grch37_coordinates",
    "grch38_coordinates",
    "haploinsufficiency_score",
    "triplosensitivity_score",
    "type_of_variants",
    "verbose_name",
    "required_overlap_percentage",
)
_STR_EXTRA_FIELDS = (
    "repeated_sequence",
    "normal_repeats",
    "pathogenic_repeats",
    "chromosome",
    "grch37_coordinates",
    "grch38_coordinates",
)

_PANEL_INSERT = (
    "INSERT OR REPLACE INTO panel ("
    "region, panel_id, hash_id, name, name_upper, version, version_created, "
    "disease_group, disease_sub_group, status, description, "
    "relevant_disorders_json, types_json, "
    "number_of_genes, number_of_regions, number_of_strs, "
    "signed_off_version, signed_off_date"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_ENTITY_INSERT = (
    "INSERT OR REPLACE INTO entity ("
    "region, panel_id, entity_type, entity_name, "
    "gene_symbol, gene_symbol_upper, hgnc_id, "
    "confidence_level, confidence_label, confidence_rank, "
    "mode_of_inheritance, penetrance, "
    "phenotypes_json, evidence_json, publications_json, omim_json, tags_json, "
    "extra_json, panel_name"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def build_database(
    config: PanelAppDataConfigModel,
    crawled: dict[str, Any],
    *,
    build_started: float | None = None,
) -> BuildMeta:
    """Build the PanelApp SQLite database from a crawl dict, atomically.

    Args:
        config: Active data configuration (paths, source URLs).
        crawled: Per-region crawl mapping from the downloader.
        build_started: Optional ``time.perf_counter()`` start; used to report
            an end-to-end duration that includes the crawl.

    Returns:
        Typed provenance for the freshly built database.
    """
    start = build_started if build_started is not None else time.perf_counter()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = config.db_path.with_suffix(".sqlite.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    panel_counts: dict[str, int] = {"uk": 0, "australia": 0}
    panel_versions: dict[str, dict[str, str]] = {"uk": {}, "australia": {}}
    entity_count = 0

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(load_schema_sql())
        for region in _REGIONS:
            region_crawl = crawled.get(region) or {}
            count, versions, n_entities = _load_region(conn, region, region_crawl)
            panel_counts[region] = count
            panel_versions[region] = versions
            entity_count += n_entities
        gene_count = _build_gene_rollup(conn)
        _build_fts(conn)

        duration = round(time.perf_counter() - start, 3)
        meta_values: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source_uk_url": config.uk_api_url,
            "source_au_url": config.au_api_url,
            "uk_panel_count": panel_counts["uk"],
            "au_panel_count": panel_counts["australia"],
            "entity_count": entity_count,
            "gene_count": gene_count,
            "build_utc": datetime.now(tz=UTC).isoformat(),
            "build_duration_s": duration,
            "panel_versions_json": json.dumps(panel_versions),
        }
        _insert_meta(conn, meta_values)
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, config.db_path)
    return BuildMeta(
        schema_version=SCHEMA_VERSION,
        source_uk_url=config.uk_api_url,
        source_au_url=config.au_api_url,
        uk_panel_count=panel_counts["uk"],
        au_panel_count=panel_counts["australia"],
        entity_count=entity_count,
        gene_count=gene_count,
        build_utc=meta_values["build_utc"],
        build_duration_s=meta_values["build_duration_s"],
    )


def _load_region(
    conn: sqlite3.Connection,
    region: str,
    region_crawl: dict[str, Any],
) -> tuple[int, dict[str, str], int]:
    """Insert one region's panels + entities; return (n_panels, versions, n_entities)."""
    details: dict[Any, Any] = region_crawl.get("details") or {}
    signed_off: dict[Any, Any] = region_crawl.get("signed_off") or {}
    # Fall back to the list payload when a panel has no fetched detail.
    list_by_id = {
        int(p["id"]): p for p in (region_crawl.get("panels") or []) if p.get("id") is not None
    }

    versions: dict[str, str] = {}
    entity_count = 0
    panel_ids = sorted({int(pid) for pid in details} | set(list_by_id))
    for panel_id in panel_ids:
        panel = details.get(panel_id) or list_by_id.get(panel_id)
        if panel is None:  # pragma: no cover - defensive
            continue
        signed = signed_off.get(panel_id) or {}
        _insert_panel(conn, region, panel_id, panel, signed)
        version = panel.get("version")
        if version is not None:
            versions[str(panel_id)] = str(version)
        if panel_id in details:
            entity_count += _insert_entities(conn, region, panel_id, details[panel_id])
    return len(panel_ids), versions, entity_count


def _insert_panel(
    conn: sqlite3.Connection,
    region: str,
    panel_id: int,
    panel: dict[str, Any],
    signed: dict[str, Any],
) -> None:
    """Insert a single panel row, merging signed-off version/date."""
    stats = panel.get("stats") or {}
    name = panel.get("name") or ""
    conn.execute(
        _PANEL_INSERT,
        (
            region,
            panel_id,
            panel.get("hash_id"),
            name,
            name.upper(),
            _as_str_or_none(panel.get("version")),
            panel.get("version_created"),
            panel.get("disease_group"),
            panel.get("disease_sub_group"),
            panel.get("status"),
            panel.get("description"),
            json.dumps(panel.get("relevant_disorders") or []),
            json.dumps(panel.get("types") or []),
            int(stats.get("number_of_genes") or 0),
            int(stats.get("number_of_regions") or 0),
            int(stats.get("number_of_strs") or 0),
            _as_str_or_none(signed.get("version")),
            signed.get("signed_off"),
        ),
    )


def _insert_entities(
    conn: sqlite3.Connection,
    region: str,
    panel_id: int,
    detail: dict[str, Any],
) -> int:
    """Explode and insert a panel's gene/region/str entities; return the count."""
    panel_name = detail.get("name") or ""
    rows: list[tuple[Any, ...]] = []
    for entity_type, key in (("gene", "genes"), ("region", "regions"), ("str", "strs")):
        for raw in detail.get(key) or []:
            rows.append(_entity_tuple(region, panel_id, entity_type, raw, panel_name))
    if rows:
        conn.executemany(_ENTITY_INSERT, rows)
    return len(rows)


def _entity_tuple(
    region: str,
    panel_id: int,
    entity_type: str,
    raw: dict[str, Any],
    panel_name: str,
) -> tuple[Any, ...]:
    """Build the insert tuple for one entity, packing type-specific extras."""
    gene_data = raw.get("gene_data") or {}
    gene_symbol = gene_data.get("gene_symbol")
    hgnc_id = gene_data.get("hgnc_id")
    level = _as_str_or_none(raw.get("confidence_level"))
    label = confidence_label(level) if level is not None else None
    rank = CONFIDENCE_RANK.get(label) if label is not None else None

    if entity_type == "region":
        extra = {f: raw.get(f) for f in _REGION_EXTRA_FIELDS if raw.get(f) not in (None, "")}
    elif entity_type == "str":
        extra = {f: raw.get(f) for f in _STR_EXTRA_FIELDS if raw.get(f) not in (None, "")}
    else:
        extra = {}

    return (
        region,
        panel_id,
        entity_type,
        raw.get("entity_name") or "",
        gene_symbol,
        gene_symbol.upper() if gene_symbol else None,
        hgnc_id,
        level,
        label,
        rank,
        raw.get("mode_of_inheritance"),
        raw.get("penetrance"),
        json.dumps(raw.get("phenotypes") or []),
        json.dumps(raw.get("evidence") or []),
        json.dumps(raw.get("publications") or []),
        json.dumps(gene_data.get("omim_gene") or []),
        json.dumps(raw.get("tags") or []),
        json.dumps(extra),
        panel_name,
    )


def _build_gene_rollup(conn: sqlite3.Connection) -> int:
    """Roll up one gene row per ``gene_symbol_upper`` across both regions."""
    rows = conn.execute(
        "SELECT gene_symbol_upper, gene_symbol, hgnc_id, region, panel_id, "
        "confidence_label, confidence_rank "
        "FROM entity WHERE gene_symbol_upper IS NOT NULL"
    ).fetchall()

    rollup: dict[str, dict[str, Any]] = {}
    for sym_upper, symbol, hgnc_id, region, panel_id, label, rank in rows:
        agg = rollup.get(sym_upper)
        if agg is None:
            agg = {
                "gene_symbol": symbol,
                "hgnc_id": hgnc_id,
                "panels": set(),
                "regions": set(),
                "max_label": None,
                "max_rank": 0,
            }
            rollup[sym_upper] = agg
        if not agg["gene_symbol"] and symbol:
            agg["gene_symbol"] = symbol
        if not agg["hgnc_id"] and hgnc_id:
            agg["hgnc_id"] = hgnc_id
        agg["panels"].add((region, panel_id))
        agg["regions"].add(region)
        if rank is not None and rank > agg["max_rank"]:
            agg["max_rank"] = rank
            agg["max_label"] = label

    insert = (
        "INSERT OR REPLACE INTO gene ("
        "gene_symbol_upper, gene_symbol, hgnc_id, panel_count, regions_json, "
        "max_confidence_label, max_confidence_rank"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    payload = [
        (
            sym_upper,
            agg["gene_symbol"],
            agg["hgnc_id"],
            len(agg["panels"]),
            json.dumps(sorted(agg["regions"])),
            agg["max_label"],
            agg["max_rank"] or None,
        )
        for sym_upper, agg in rollup.items()
    ]
    if payload:
        conn.executemany(insert, payload)
    return len(payload)


def _build_fts(conn: sqlite3.Connection) -> None:
    """Populate the panel_fts index from the panel table."""
    rows = conn.execute(
        "SELECT region, panel_id, name, relevant_disorders_json, disease_group FROM panel"
    ).fetchall()
    payload: list[tuple[Any, ...]] = []
    for region, panel_id, name, disorders_json, disease_group in rows:
        disorders = json.loads(disorders_json) if disorders_json else []
        payload.append((region, panel_id, name or "", " ".join(disorders), disease_group or ""))
    if payload:
        conn.executemany(
            "INSERT INTO panel_fts (region, panel_id, name, relevant_disorders, disease_group) "
            "VALUES (?, ?, ?, ?, ?)",
            payload,
        )


def _insert_meta(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    """Insert the single provenance row (``id = 1``)."""
    columns = list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO meta (id, {', '.join(columns)}) VALUES (1, {placeholders})",
        tuple(values[col] for col in columns),
    )


def _as_str_or_none(value: Any) -> str | None:
    """Cast a value to ``str`` (PanelApp versions/levels arrive as int or str)."""
    return None if value is None else str(value)


def read_build_meta(db_path: Any) -> BuildMeta:
    """Read the provenance row from an existing database into a ``BuildMeta``."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:  # pragma: no cover - defensive
        raise DataUnavailableError("PanelApp database has no provenance (meta) row.")
    return BuildMeta(
        schema_version=row["schema_version"],
        source_uk_url=row["source_uk_url"],
        source_au_url=row["source_au_url"],
        uk_panel_count=row["uk_panel_count"],
        au_panel_count=row["au_panel_count"],
        entity_count=row["entity_count"],
        gene_count=row["gene_count"],
        build_utc=row["build_utc"],
        build_duration_s=row["build_duration_s"],
    )


def _read_panel_versions(db_path: Any) -> dict[str, dict[str, str]]:
    """Read ``panel_versions_json`` from an existing database, or empty."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT panel_versions_json FROM meta WHERE id = 1").fetchone()
    except sqlite3.Error:  # pragma: no cover - missing/old db
        return {}
    finally:
        conn.close()
    if not row or not row[0]:
        return {}
    loaded = json.loads(row[0])
    return loaded if isinstance(loaded, dict) else {}


def _versions_from_listing(listing: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Build a ``{region:{panel_id:version}}`` map from a list-only crawl."""
    versions: dict[str, dict[str, str]] = {}
    for region in _REGIONS:
        region_versions: dict[str, str] = {}
        for panel in (listing.get(region) or {}).get("panels") or []:
            pid = panel.get("id")
            if pid is None:
                continue
            region_versions[str(int(pid))] = str(panel.get("version"))
        versions[region] = region_versions
    return versions


async def refresh(
    config: PanelAppDataConfigModel,
    *,
    force: bool = False,
    client: PanelAppRestClient | None = None,
) -> BuildMeta:
    """Crawl both regions and conditionally rebuild the database under the lock.

    When ``force`` is false and a database already exists, panels are listed
    first and their versions compared against the stored ``panel_versions_json``.
    If nothing changed, the existing provenance is returned without a rebuild.
    Otherwise a full crawl + atomic rebuild runs (entities for every panel are
    re-fetched so the resulting database is internally consistent).

    Args:
        config: Active data configuration.
        force: When ``True``, always do a full crawl + rebuild.
        client: Optional injected REST client (for tests).

    Returns:
        Typed provenance for the resulting database.
    """
    start = time.perf_counter()
    with build_lock(config.data_dir, timeout=config.build_lock_timeout):
        if not force and config.db_path.exists():
            listing = await crawl_all(config, client=client, only_panel_ids={})
            previous = _read_panel_versions(config.db_path)
            if _versions_from_listing(listing) == previous:
                return read_build_meta(config.db_path)
        crawled = await crawl_all(config, client=client)
        return build_database(config, crawled, build_started=start)
