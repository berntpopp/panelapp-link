"""Tests for logging_config: configure_logging() with json/console formats."""

from __future__ import annotations

import logging

import pytest
import structlog

from panelapp_link import logging_config
from panelapp_link.config import settings


@pytest.fixture(autouse=True)
def _restore_logging() -> None:
    """Snapshot/restore the global logging + structlog state around each test."""
    orig_format = settings.log_format
    orig_level = settings.log_level
    yield
    settings.log_format = orig_format
    settings.log_level = orig_level
    structlog.reset_defaults()
    logging.getLogger().setLevel(logging.WARNING)


def test_configure_logging_json_returns_bound_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    logger = logging_config.configure_logging()
    assert logger is not None
    # The returned logger logs without raising under the JSON renderer.
    logger.info("hello", extra_field="x")


def test_configure_logging_console_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "console")
    logger = logging_config.configure_logging()
    assert logger is not None
    logger.info("hello console")


def test_configure_logging_debug_level_enables_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    # DEBUG drives the colored console renderer + the verbose third-party levels.
    monkeypatch.setattr(settings, "log_format", "console")
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    logger = logging_config.configure_logging()
    assert logger is not None
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    # Noisy third-party loggers were retuned for debug.
    assert logging.getLogger("uvicorn.access").level == logging.INFO


def test_configure_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    first = logging_config.configure_logging()
    second = logging_config.configure_logging()
    assert first is not None
    assert second is not None
    # Re-configuring does not pile up duplicate root handlers (it clears first).
    assert len(logging.getLogger().handlers) == 1


def test_add_static_fields_sets_service_and_version() -> None:
    out = logging_config._add_static_fields(None, "info", {})
    assert out["service"] == "panelapp-link"
    assert out["version"]
    # Existing values are preserved (setdefault, not overwrite).
    preset = logging_config._add_static_fields(None, "info", {"service": "custom"})
    assert preset["service"] == "custom"
