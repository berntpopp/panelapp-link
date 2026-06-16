"""Tests for panelapp_link.config settings loading and env prefix."""

from __future__ import annotations

from pathlib import Path

import pytest

from panelapp_link.config import (
    PanelAppDataConfigModel,
    ServerSettings,
    get_data_config,
    settings,
)


def test_settings_instantiate_with_defaults() -> None:
    """ServerSettings builds with defaults and a nested data config."""
    s = ServerSettings()
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.transport in ("unified", "http", "stdio")
    assert s.mcp_path == "/mcp"
    assert isinstance(s.data, PanelAppDataConfigModel)


def test_data_config_defaults() -> None:
    """PanelAppDataConfigModel carries the spec defaults."""
    data = PanelAppDataConfigModel()
    assert data.uk_api_url == "https://panelapp.genomicsengland.co.uk/api/v1"
    assert data.au_api_url == "https://panelapp-aus.org/api/v1"
    assert data.db_filename == "panelapp.sqlite"
    assert "PanelApp-Link/" in data.user_agent


def test_db_path_is_data_dir_join_filename(tmp_path: Path) -> None:
    """db_path == data_dir / db_filename."""
    data = PanelAppDataConfigModel(data_dir=tmp_path, db_filename="custom.sqlite")
    assert data.db_path == tmp_path / "custom.sqlite"


def test_get_data_config_returns_singleton_data() -> None:
    """get_data_config() returns the global settings.data."""
    assert get_data_config() is settings.data


def test_env_prefix_overrides_server_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """PANELAPP_LINK_ prefix overrides top-level fields."""
    monkeypatch.setenv("PANELAPP_LINK_PORT", "9123")
    s = ServerSettings()
    assert s.port == 9123


def test_nested_env_override_db_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    """PANELAPP_LINK_DATA__DB_FILENAME overrides the nested data config."""
    monkeypatch.setenv("PANELAPP_LINK_DATA__DB_FILENAME", "override.sqlite")
    s = ServerSettings()
    assert s.data.db_filename == "override.sqlite"
    assert s.data.db_path.name == "override.sqlite"
