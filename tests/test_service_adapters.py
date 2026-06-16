"""Tests for the MCP service-adapter singleton (panelapp_link.mcp.service_adapters).

Covers the lazy live-service singleton built over a shared REST client, the
test-override path, and the reset (close + drop) path. No database, no network.
"""

from __future__ import annotations

import pytest

from panelapp_link.config import PanelAppDataConfigModel
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


def _point_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PanelAppDataConfigModel(
        uk_api_url="https://uk.example.test/api/v1",
        au_api_url="https://au.example.test/api/v1",
    )
    monkeypatch.setattr(service_adapters, "get_data_config", lambda: config)


def test_get_service_builds_live_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config(monkeypatch)
    service = service_adapters.get_panelapp_service()
    assert isinstance(service, PanelAppService)
    caps = service.capabilities_data()
    assert caps["mode"] == "live"
    assert caps["sources"]["uk"] == "https://uk.example.test/api/v1"


def test_get_service_caches_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config(monkeypatch)
    first = service_adapters.get_panelapp_service()
    second = service_adapters.get_panelapp_service()
    assert first is second


def test_override_short_circuits_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    service_adapters.set_service_for_testing(sentinel)  # type: ignore[arg-type]

    def _boom() -> None:
        raise AssertionError("get_data_config must not run when override is set")

    monkeypatch.setattr(service_adapters, "get_data_config", _boom)
    assert service_adapters.get_panelapp_service() is sentinel


def test_reset_drops_cache_and_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    _point_config(monkeypatch)
    first = service_adapters.get_panelapp_service()
    service_adapters.reset_panelapp_service()
    second = service_adapters.get_panelapp_service()
    assert second is not first


async def test_reset_closes_owned_client_in_async_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Inside a running loop, reset schedules the client close as a task; this
    # exercises the loop-present branch of _close_client without raising.
    _point_config(monkeypatch)
    service_adapters.get_panelapp_service()
    service_adapters.reset_panelapp_service()
    assert service_adapters.get_panelapp_service() is not None
