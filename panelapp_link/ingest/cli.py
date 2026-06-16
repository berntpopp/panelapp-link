"""Command-line interface for building and refreshing the PanelApp database.

Exposed as the ``panelapp-link-data`` console script. Provides ``build`` (force
a full crawl + rebuild), ``refresh`` (conditional rebuild), and ``status``
(print provenance of the existing database).
"""

from __future__ import annotations

import asyncio
import sqlite3

import typer

from panelapp_link.config import get_data_config
from panelapp_link.exceptions import DownloadError, RateLimitError
from panelapp_link.ingest.builder import refresh
from panelapp_link.models.records import BuildMeta

app = typer.Typer(
    add_completion=False,
    help="Build and refresh the local PanelApp SQLite database.",
)


def _print_summary(meta: BuildMeta, *, header: str) -> None:
    """Print a compact provenance summary for a build."""
    print(header)
    print(f"  schema_version : {meta.schema_version}")
    print(f"  source_uk_url  : {meta.source_uk_url}")
    print(f"  source_au_url  : {meta.source_au_url}")
    print(f"  uk_panels      : {meta.uk_panel_count}")
    print(f"  au_panels      : {meta.au_panel_count}")
    print(f"  entities       : {meta.entity_count}")
    print(f"  genes          : {meta.gene_count}")
    print(f"  built_utc      : {meta.build_utc}")
    if meta.build_duration_s is not None:
        print(f"  build_seconds  : {meta.build_duration_s}")


def _run_refresh(*, force: bool) -> BuildMeta:
    """Run a crawl + (conditional) rebuild, mapping API errors to exit code 1."""
    config = get_data_config()
    try:
        return asyncio.run(refresh(config, force=force))
    except RateLimitError as exc:
        print(f"ERROR: PanelApp rate-limited the crawl: {exc}")
        raise typer.Exit(code=1) from exc
    except DownloadError as exc:
        print(f"ERROR: crawl failed: {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def build() -> None:
    """Force a full crawl and rebuild of the database."""
    meta = _run_refresh(force=True)
    _print_summary(meta, header="Built PanelApp database:")


def refresh_cmd() -> None:
    """Conditionally refresh the database; rebuild only if a panel changed."""
    meta = _run_refresh(force=False)
    _print_summary(meta, header="PanelApp database refreshed:")


@app.command()
def status() -> None:
    """Print provenance of the existing database, or a hint to build it."""
    config = get_data_config()
    if not config.db_path.exists():
        print(f"No PanelApp database at {config.db_path}.")
        print("Run `panelapp-link-data build` to crawl and build it.")
        raise typer.Exit(code=1)
    conn = sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        print("Database exists but has no provenance (meta) row.")
        print("Run `panelapp-link-data build` to rebuild it.")
        raise typer.Exit(code=1)
    meta = BuildMeta(
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
    _print_summary(meta, header=f"PanelApp database at {config.db_path}:")


# Register the refresh command under its CLI name ("refresh"); the function is
# named refresh_cmd to avoid shadowing the imported builder.refresh.
app.command(name="refresh")(refresh_cmd)


def main() -> None:
    """Console-script entry point for ``panelapp-link-data``."""
    app()


if __name__ == "__main__":
    main()
