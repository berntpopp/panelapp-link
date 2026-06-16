"""Local SQLite data store: schema loader and read-only repository."""

from __future__ import annotations

from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def load_schema_sql() -> str:
    """Return the DDL used to build the PanelApp-Link SQLite database."""
    return SCHEMA_PATH.read_text(encoding="utf-8")
