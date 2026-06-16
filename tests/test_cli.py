"""Tests for the panelapp-link-data CLI (build/refresh/status)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DownloadError, RateLimitError
from panelapp_link.ingest import cli
from panelapp_link.models.records import BuildMeta

runner = CliRunner()


def _fake_meta() -> BuildMeta:
    return BuildMeta(
        schema_version="1",
        source_uk_url="https://uk.example/api",
        source_au_url="https://au.example/api",
        uk_panel_count=2,
        au_panel_count=1,
        entity_count=10,
        gene_count=7,
        build_utc="2026-01-01T00:00:00Z",
        build_duration_s=1.5,
    )


def test_status_reports_provenance(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`status` over a built DB prints provenance and exits 0."""
    config = PanelAppDataConfigModel(data_dir=built_db.parent, db_filename=built_db.name)
    monkeypatch.setattr(cli, "get_data_config", lambda: config)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0, result.output
    assert "PanelApp database at" in result.output
    assert "uk_panels" in result.output


def test_status_missing_db_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`status` with no database exits 1 and hints at `build`."""
    config = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="absent.sqlite")
    monkeypatch.setattr(cli, "get_data_config", lambda: config)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 1
    assert "No PanelApp database" in result.output


# --- build -----------------------------------------------------------------


def test_build_success_prints_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build` runs a forced refresh and prints the provenance summary (exit 0)."""
    seen_force: list[bool] = []

    async def fake_refresh(_cfg: Any, *, force: bool) -> BuildMeta:
        seen_force.append(force)
        return _fake_meta()

    monkeypatch.setattr(cli, "refresh", fake_refresh)
    monkeypatch.setattr(cli, "get_data_config", lambda: PanelAppDataConfigModel())
    result = runner.invoke(cli.app, ["build"])
    assert result.exit_code == 0, result.output
    assert seen_force == [True]  # build forces a full crawl
    assert "Built PanelApp database:" in result.output
    assert "gene_count" not in result.output  # summary uses padded labels
    assert "genes          : 7" in result.output


def test_build_download_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_cfg: Any, *, force: bool) -> BuildMeta:
        raise DownloadError("network down")

    monkeypatch.setattr(cli, "refresh", boom)
    monkeypatch.setattr(cli, "get_data_config", lambda: PanelAppDataConfigModel())
    result = runner.invoke(cli.app, ["build"])
    assert result.exit_code == 1
    assert "crawl failed" in result.output


def test_build_rate_limit_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_cfg: Any, *, force: bool) -> BuildMeta:
        raise RateLimitError("429")

    monkeypatch.setattr(cli, "refresh", boom)
    monkeypatch.setattr(cli, "get_data_config", lambda: PanelAppDataConfigModel())
    result = runner.invoke(cli.app, ["build"])
    assert result.exit_code == 1
    assert "rate-limited" in result.output


# --- refresh ---------------------------------------------------------------


def test_refresh_success_prints_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh` runs a conditional (force=False) refresh and prints a summary."""
    seen_force: list[bool] = []

    async def fake_refresh(_cfg: Any, *, force: bool) -> BuildMeta:
        seen_force.append(force)
        return _fake_meta()

    monkeypatch.setattr(cli, "refresh", fake_refresh)
    monkeypatch.setattr(cli, "get_data_config", lambda: PanelAppDataConfigModel())
    result = runner.invoke(cli.app, ["refresh"])
    assert result.exit_code == 0, result.output
    assert seen_force == [False]  # refresh is conditional
    assert "PanelApp database refreshed:" in result.output


def test_refresh_download_error_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_cfg: Any, *, force: bool) -> BuildMeta:
        raise DownloadError("offline")

    monkeypatch.setattr(cli, "refresh", boom)
    monkeypatch.setattr(cli, "get_data_config", lambda: PanelAppDataConfigModel())
    result = runner.invoke(cli.app, ["refresh"])
    assert result.exit_code == 1
    assert "crawl failed" in result.output
