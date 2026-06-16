"""Read-only SQLite repository for the built PanelApp index.

All aggregation (entity explosion, gene roll-up, FTS) is pre-computed by the
ingest builder, so this layer only reads rows and decodes the JSON columns back
into Python objects. FTS5 queries are sanitized so raw user text never reaches
``MATCH`` (which can raise on operator characters), with a ``LIKE`` fallback for
pathological input and an empty-query "all panels by name" path.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from panelapp_link.exceptions import DataUnavailableError

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# entity columns that hold JSON-encoded lists.
_ENTITY_LIST_JSON = (
    "phenotypes_json",
    "evidence_json",
    "publications_json",
    "omim_json",
    "tags_json",
)


class PanelAppRepository:
    """Read-only access to the built PanelApp SQLite index."""

    def __init__(self, db_path: Path | str) -> None:
        """Open a read-only connection to the PanelApp database."""
        self._path = Path(db_path)
        if not self._path.exists():
            raise DataUnavailableError(
                f"PanelApp database not found at {self._path}. "
                "Build it with `panelapp-link-data build`."
            )
        try:
            self._conn = sqlite3.connect(
                f"file:{self._path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:  # pragma: no cover - rare OS-level failure
            raise DataUnavailableError(
                f"Cannot open PanelApp database at {self._path}: {exc}."
            ) from exc
        self._conn.row_factory = sqlite3.Row

    # -- provenance ------------------------------------------------------------

    def get_meta(self) -> dict[str, Any]:
        """Return build provenance from the ``meta`` table (decodes versions JSON)."""
        try:
            row = self._conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise DataUnavailableError(
                f"PanelApp database at {self._path} is unreadable: {exc}."
            ) from exc
        if row is None:
            raise DataUnavailableError(f"PanelApp database at {self._path} has no build metadata.")
        meta = dict(row)
        raw = meta.get("panel_versions_json")
        meta["panel_versions"] = json.loads(raw) if raw else {}
        return meta

    # -- panels ----------------------------------------------------------------

    @staticmethod
    def _panel_from_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["relevant_disorders"] = _decode_list(record.pop("relevant_disorders_json", None))
        record["types"] = _decode_list(record.pop("types_json", None))
        return record

    def get_panel(self, region: str, panel_id: int) -> dict[str, Any] | None:
        """Return one panel row (with decoded JSON), or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT * FROM panel WHERE region = ? AND panel_id = ?",
            (region, panel_id),
        ).fetchone()
        if row is None:
            return None
        record = self._panel_from_row(row)
        record["entity_counts"] = self._entity_counts(region, panel_id)
        return record

    def _entity_counts(self, region: str, panel_id: int) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM entity "
            "WHERE region = ? AND panel_id = ? GROUP BY entity_type",
            (region, panel_id),
        ).fetchall()
        return {r["entity_type"]: r["n"] for r in rows}

    def search_panels(
        self,
        query: str,
        regions: list[str],
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search panels by name/disorders/disease group, filtered by region.

        An empty query lists all panels (by name). Otherwise an FTS5 ``MATCH`` is
        attempted, falling back to a ``LIKE`` scan if the sanitized query is
        rejected.
        """
        region_clause, region_params = _region_clause(regions, column="p.region")
        if not (query or "").strip():
            return self._search_all(region_clause, region_params, limit, offset)
        match = _fts_query(query)
        sql = (
            "SELECT p.* FROM panel_fts f "
            "JOIN panel p ON p.region = f.region AND p.panel_id = f.panel_id "
            "WHERE panel_fts MATCH ?"
            f"{region_clause} "
            "ORDER BY rank, p.name LIMIT ? OFFSET ?"
        )
        try:
            rows = self._conn.execute(sql, (match, *region_params, limit, offset)).fetchall()
        except sqlite3.Error:
            rows = self._search_like(query, region_clause, region_params, limit, offset)
        return [self._panel_from_row(r) for r in rows]

    def _search_all(
        self,
        region_clause: str,
        region_params: list[str],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        where = f"WHERE 1=1{region_clause}" if region_clause else ""
        sql = f"SELECT p.* FROM panel p {where} ORDER BY p.name LIMIT ? OFFSET ?"
        rows = self._conn.execute(sql, (*region_params, limit, offset)).fetchall()
        return [self._panel_from_row(r) for r in rows]

    def _search_like(
        self,
        query: str,
        region_clause: str,
        region_params: list[str],
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        pattern = "%" + query.replace("%", "").replace("_", "").upper() + "%"
        sql = (
            "SELECT p.* FROM panel p "
            "WHERE p.name_upper LIKE ?"
            f"{region_clause} "
            "ORDER BY p.name LIMIT ? OFFSET ?"
        )
        return self._conn.execute(sql, (pattern, *region_params, limit, offset)).fetchall()

    # -- entities --------------------------------------------------------------

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for col in _ENTITY_LIST_JSON:
            key = col[: -len("_json")]
            record[key] = _decode_list(record.pop(col, None))
        record["extra"] = _decode_obj(record.pop("extra_json", None))
        return record

    def get_panel_entities(
        self,
        region: str,
        panel_id: int,
        entity_type: str,
        min_rank: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a panel's entities, optionally filtered by type and confidence.

        ``entity_type == "all"`` removes the type filter; ``min_rank`` keeps only
        entities whose ``confidence_rank`` is at least that value.
        """
        clauses = ["region = ?", "panel_id = ?"]
        params: list[Any] = [region, panel_id]
        if entity_type != "all":
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if min_rank is not None:
            clauses.append("confidence_rank >= ?")
            params.append(min_rank)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM entity WHERE {where} "
            "ORDER BY confidence_rank DESC, entity_type, entity_name "
            "LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._entity_from_row(r) for r in rows]

    # -- gene -> panels --------------------------------------------------------

    def get_gene_panels(
        self,
        *,
        gene_symbol_upper: str | None = None,
        hgnc_id: str | None = None,
        regions: list[str],
        min_rank: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return panels a gene appears on (one row per panel), across regions.

        Exactly one of ``gene_symbol_upper`` or ``hgnc_id`` selects the gene.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if hgnc_id is not None:
            clauses.append("hgnc_id = ?")
            params.append(hgnc_id)
        elif gene_symbol_upper is not None:
            clauses.append("gene_symbol_upper = ?")
            params.append(gene_symbol_upper)
        else:  # pragma: no cover - guarded by caller/service
            return []
        region_in, region_params = _region_in_clause(regions, column="region")
        if region_in:
            clauses.append(region_in)
            params.extend(region_params)
        if min_rank is not None:
            clauses.append("confidence_rank >= ?")
            params.append(min_rank)
        where = " AND ".join(clauses)
        sql = (
            "SELECT region, panel_id, panel_name, gene_symbol, hgnc_id, "
            "confidence_level, confidence_label, confidence_rank, mode_of_inheritance "
            f"FROM entity WHERE {where} "
            "ORDER BY confidence_rank DESC, region, panel_name"
        )
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    # -- gene roll-up ----------------------------------------------------------

    @staticmethod
    def _gene_from_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["regions"] = _decode_list(record.pop("regions_json", None))
        return record

    def resolve_gene(
        self,
        *,
        gene_symbol_upper: str | None = None,
        hgnc_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return rolled-up gene rows matching a symbol or HGNC id (possibly many)."""
        if hgnc_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM gene WHERE hgnc_id = ? ORDER BY gene_symbol_upper",
                (hgnc_id,),
            ).fetchall()
        elif gene_symbol_upper is not None:
            rows = self._conn.execute(
                "SELECT * FROM gene WHERE gene_symbol_upper = ?",
                (gene_symbol_upper,),
            ).fetchall()
        else:  # pragma: no cover - guarded by caller/service
            return []
        return [self._gene_from_row(r) for r in rows]

    def get_gene(self, gene_symbol_upper: str) -> dict[str, Any] | None:
        """Return the rolled-up gene row for a symbol, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM gene WHERE gene_symbol_upper = ?",
            (gene_symbol_upper,),
        ).fetchone()
        return self._gene_from_row(row) if row is not None else None

    def close(self) -> None:
        """Release the underlying database connection."""
        self._conn.close()


def _decode_list(value: Any) -> list[Any]:
    """Decode a JSON list column, tolerating ``None``/empty as ``[]``."""
    if not value:
        return []
    decoded = json.loads(value)
    return decoded if isinstance(decoded, list) else []


def _decode_obj(value: Any) -> dict[str, Any]:
    """Decode a JSON object column, tolerating ``None``/empty as ``{}``."""
    if not value:
        return {}
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else {}


def _region_in_clause(regions: list[str], *, column: str) -> tuple[str, list[str]]:
    """Build a bare ``region IN (...)`` clause (empty when ``regions`` is falsy)."""
    if not regions:
        return "", []
    placeholders = ", ".join("?" for _ in regions)
    return f"{column} IN ({placeholders})", list(regions)


def _region_clause(regions: list[str], *, column: str) -> tuple[str, list[str]]:
    """Build an ``AND region IN (...)`` clause (empty when ``regions`` is falsy)."""
    clause, params = _region_in_clause(regions, column=column)
    return (f" AND {clause}", params) if clause else ("", [])


def _fts_query(text: str) -> str:
    """Build a safe FTS5 MATCH string (token OR, last token prefix-matched)."""
    tokens = _FTS_TOKEN_RE.findall(text or "")
    if not tokens:
        return '""'
    quoted = [f'"{tok}"' for tok in tokens[:-1]]
    quoted.append(f'"{tokens[-1]}"*')
    return " OR ".join(quoted)
