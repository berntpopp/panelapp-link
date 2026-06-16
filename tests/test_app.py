"""Integration tests for the PanelApp-Link facade, app, and entry points.

These tests construct the FastMCP facade and the FastAPI app without any network
access. App construction (and the facade) never touch the network; the live
backend only makes requests when a tool is invoked, never at construction or
startup, so none of these tests hit PanelApp.
"""

from __future__ import annotations

import importlib
import warnings

from fastapi import FastAPI

from panelapp_link.config import settings
from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.server_manager import UnifiedServerManager, create_app

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

EXPECTED_TOOLS = {
    "search_panels",
    "get_panel",
    "get_panel_genes",
    "get_gene_panels",
    "resolve_gene",
    "get_server_capabilities",
    "get_panelapp_diagnostics",
}


# --- Facade ---------------------------------------------------------------


async def test_facade_builds_and_exposes_seven_tools() -> None:
    mcp = create_panelapp_mcp()
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS
    assert len(names) == 7


def test_facade_is_named_and_instructed() -> None:
    mcp = create_panelapp_mcp()
    assert mcp.name == "panelapp-link"
    assert mcp.instructions


# --- FastAPI app (no database, no network) --------------------------------


def test_create_app_returns_fastapi() -> None:
    assert isinstance(create_app(), FastAPI)


def test_server_manager_builds_app() -> None:
    manager = UnifiedServerManager()
    assert isinstance(manager.build_app(), FastAPI)


def test_app_has_health_route() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert "/api/health" in paths


def test_health_endpoint_reports_live_status() -> None:
    # TestClient does not enter the lifespan unless used as a context manager,
    # so nothing touches the network here.
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    # The live backend reports its mode + sources without any network call.
    assert body["data"]["mode"] == "live"
    assert "uk" in body["data"]["sources"]


def test_root_advertises_mcp_endpoint() -> None:
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "PanelApp-Link"
    assert body["mcp_endpoint"] == settings.mcp_path


def test_mcp_mounts_at_configured_path() -> None:
    # Compose the app + MCP exactly like start_unified_server does, but without
    # serving it, and assert the MCP ASGI app is mounted at settings.mcp_path.
    app = create_app()
    mcp = create_panelapp_mcp()
    mcp_asgi = mcp.http_app(path=settings.mcp_path)
    app.mount("/", mcp_asgi)
    mounted = [route for route in app.routes if getattr(route, "path", None) == ""]
    assert mounted, "expected the MCP ASGI app to be mounted"
    # The MCP streamable-http endpoint lives at settings.mcp_path within the mount.
    sub_paths = {
        getattr(route, "path", None)
        for mount in mounted
        for route in getattr(getattr(mount, "app", None), "routes", [])
    }
    assert settings.mcp_path in sub_paths


# --- Entry points ---------------------------------------------------------


def test_server_entrypoint_exposes_main() -> None:
    module = importlib.import_module("server")
    assert callable(module.main)


def test_mcp_server_entrypoint_exposes_main() -> None:
    module = importlib.import_module("mcp_server")
    assert callable(module.main)
