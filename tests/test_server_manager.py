"""Tests for UnifiedServerManager lifecycle helpers.

These cover the parts of server_manager.py that do not bind a socket or start
uvicorn: the stdio environment defaults and the graceful shutdown contract.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from panelapp_link.server_manager import UnifiedServerManager


def test_configure_stdio_environment_sets_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear the env vars the helper manages so we observe it setting them.
    for key in (
        "PYTHONUNBUFFERED",
        "PANELAPP_LINK_TRANSPORT",
        "FASTMCP_DISABLE_BANNER",
        "FASTMCP_NO_BANNER",
        "FASTMCP_QUIET",
        "NO_COLOR",
        "FORCE_COLOR",
        "TERM",
        "PYTHONWARNINGS",
    ):
        monkeypatch.delenv(key, raising=False)

    UnifiedServerManager._configure_stdio_environment()

    assert os.environ["PANELAPP_LINK_TRANSPORT"] == "stdio"
    assert os.environ["FASTMCP_DISABLE_BANNER"] == "1"
    assert os.environ["NO_COLOR"] == "1"
    assert os.environ["TERM"] == "dumb"


def test_configure_stdio_environment_does_not_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    # setdefault semantics: an existing value must be preserved.
    monkeypatch.setenv("PANELAPP_LINK_TRANSPORT", "http")
    UnifiedServerManager._configure_stdio_environment()
    assert os.environ["PANELAPP_LINK_TRANSPORT"] == "http"


async def test_shutdown_without_server_is_safe() -> None:
    manager = UnifiedServerManager()
    # No uvicorn server was ever created -> shutdown must be a no-op, not raise.
    await manager.shutdown()


async def test_shutdown_signals_running_server() -> None:
    manager = UnifiedServerManager()

    class _FakeServer:
        should_exit = False

    fake = _FakeServer()
    manager._uvicorn_server = fake  # type: ignore[assignment]
    await manager.shutdown()
    assert fake.should_exit is True


async def test_shutdown_logs_when_logger_present() -> None:
    logged: list[str] = []

    class _Logger:
        def info(self, msg: str, **_kw: Any) -> None:
            logged.append(msg)

    manager = UnifiedServerManager(logger=_Logger())  # type: ignore[arg-type]
    await manager.shutdown()
    assert any("Shutdown" in m for m in logged)
