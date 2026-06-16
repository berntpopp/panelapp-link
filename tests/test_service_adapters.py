"""Tests for the MCP service-adapter singleton (panelapp_link.mcp.service_adapters).

Covers building the service over a real fixture DB, the test-override path, the
reset/close path, and the mtime-based hot-reload that picks up a swapped DB.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DataUnavailableError
from panelapp_link.mcp import service_adapters
from panelapp_link.services.panelapp_service import PanelAppService


@pytest.fixture(autouse=True)
def _clean_singleton() -> None:
    """Ensure no override/cache leaks across tests."""
    service_adapters.set_service_for_testing(None)
    service_adapters.reset_panelapp_service()
    yield
    service_adapters.set_service_for_testing(None)
    service_adapters.reset_panelapp_service()


def _point_config_at(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    config = PanelAppDataConfigModel(data_dir=db_path.parent, db_filename=db_path.name)
    monkeypatch.setattr(service_adapters, "get_data_config", lambda: config)


def test_get_service_builds_over_real_db(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config_at(monkeypatch, built_db)
    service = service_adapters.get_panelapp_service()
    assert isinstance(service, PanelAppService)
    # It is usable against the built fixtures.
    caps = service.capabilities_data()
    assert caps["status"] == "ok"


def test_get_service_caches_same_instance(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config_at(monkeypatch, built_db)
    first = service_adapters.get_panelapp_service()
    second = service_adapters.get_panelapp_service()
    assert first is second  # mtime unchanged -> cached instance reused


def test_missing_db_raises_data_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_config_at(monkeypatch, tmp_path / "absent.sqlite")
    with pytest.raises(DataUnavailableError):
        service_adapters.get_panelapp_service()


def test_override_short_circuits_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    service_adapters.set_service_for_testing(sentinel)  # type: ignore[arg-type]

    def _boom() -> None:
        raise AssertionError("get_data_config must not run when override is set")

    monkeypatch.setattr(service_adapters, "get_data_config", _boom)
    assert service_adapters.get_panelapp_service() is sentinel


def test_hot_reload_on_mtime_change(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config_at(monkeypatch, built_db)
    first = service_adapters.get_panelapp_service()
    # Bump the file mtime forward to simulate an atomic swap by a refresh.
    stat = built_db.stat()
    os.utime(built_db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    second = service_adapters.get_panelapp_service()
    assert second is not first  # the changed mtime forced a reopen


def test_reset_closes_and_drops_cache(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config_at(monkeypatch, built_db)
    first = service_adapters.get_panelapp_service()
    service_adapters.reset_panelapp_service()
    second = service_adapters.get_panelapp_service()
    # After a reset the singleton is rebuilt (a new instance).
    assert second is not first
