"""Tests for the panelapp-link-data CLI (status over a built fixtures DB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.ingest import cli

runner = CliRunner()


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
