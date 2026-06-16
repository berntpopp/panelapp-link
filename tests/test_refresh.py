"""Tests for the in-process refresh scheduler (panelapp_link.services.refresh).

We never sleep or run the real interval loop: we drive a single conditional
refresh tick (``_run_once``) with the builder's ``refresh`` monkeypatched to an
async stub, and exercise the enabled/disabled build guard and start/stop.
"""

from __future__ import annotations

from typing import Any

import pytest

import panelapp_link.ingest.builder as builder_mod
from panelapp_link.config import PanelAppDataConfigModel
from panelapp_link.exceptions import DownloadError
from panelapp_link.models.records import BuildMeta
from panelapp_link.services.refresh import RefreshScheduler, build_scheduler, get_active_scheduler


def _meta(build_utc: str) -> BuildMeta:
    return BuildMeta(
        schema_version="1",
        source_uk_url="uk",
        source_au_url="au",
        build_utc=build_utc,
    )


@pytest.fixture
def config() -> PanelAppDataConfigModel:
    return PanelAppDataConfigModel()


# --- build_scheduler guard -------------------------------------------------


def test_build_scheduler_disabled_returns_none(config: PanelAppDataConfigModel) -> None:
    config = config.model_copy(update={"refresh_enabled": False})
    assert build_scheduler(config) is None


def test_build_scheduler_enabled_returns_scheduler(config: PanelAppDataConfigModel) -> None:
    sched = build_scheduler(config)
    assert isinstance(sched, RefreshScheduler)
    # Interval derives from the configured hours by default.
    assert sched.status["interval_seconds"] == config.refresh_interval_hours * 3600.0


def test_scheduler_explicit_interval_and_jitter(config: PanelAppDataConfigModel) -> None:
    sched = RefreshScheduler(config, interval_seconds=5.0, jitter_seconds=0.0)
    assert sched.status["interval_seconds"] == 5.0


# --- _run_once: change detection / reload ----------------------------------


async def test_run_once_first_tick_records_ok_no_change(
    config: PanelAppDataConfigModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_refresh(_cfg: Any, *, force: bool) -> BuildMeta:
        assert force is False  # the scheduler always does a conditional refresh
        return _meta("2026-01-01T00:00:00Z")

    monkeypatch.setattr(builder_mod, "refresh", fake_refresh)
    sched = RefreshScheduler(config, interval_seconds=999.0, jitter_seconds=0.0)
    await sched._run_once()
    status = sched.status
    assert status["state"] == "ok"
    # First tick can never be a "change" (no prior build_utc baseline).
    assert status["last_changed"] is False
    assert status["last_error"] is None


async def test_run_once_detects_change_and_reloads(
    config: PanelAppDataConfigModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = iter(["2026-01-01T00:00:00Z", "2026-02-02T00:00:00Z"])

    async def fake_refresh(_cfg: Any, *, force: bool) -> BuildMeta:
        return _meta(next(builds))

    reloaded: list[bool] = []
    monkeypatch.setattr(builder_mod, "refresh", fake_refresh)
    monkeypatch.setattr(RefreshScheduler, "_reload", staticmethod(lambda: reloaded.append(True)))

    sched = RefreshScheduler(config, interval_seconds=999.0, jitter_seconds=0.0)
    await sched._run_once()  # baseline build_utc set
    assert reloaded == []
    await sched._run_once()  # new build_utc -> change -> reload
    assert reloaded == [True]
    assert sched.status["last_changed"] is True


async def test_run_once_download_error_recorded_not_raised(
    config: PanelAppDataConfigModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(_cfg: Any, *, force: bool) -> BuildMeta:
        raise DownloadError("upstream down")

    monkeypatch.setattr(builder_mod, "refresh", boom)
    sched = RefreshScheduler(config, interval_seconds=999.0, jitter_seconds=0.0)
    await sched._run_once()  # must not raise; the loop must survive
    status = sched.status
    assert status["state"] == "error"
    assert "download" in status["last_error"]


async def test_run_once_generic_error_recorded_not_raised(
    config: PanelAppDataConfigModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(_cfg: Any, *, force: bool) -> BuildMeta:
        raise ValueError("unexpected")

    monkeypatch.setattr(builder_mod, "refresh", boom)
    sched = RefreshScheduler(config, interval_seconds=999.0, jitter_seconds=0.0)
    await sched._run_once()
    status = sched.status
    assert status["state"] == "error"
    assert "ValueError" in status["last_error"]


# --- start/stop ------------------------------------------------------------


async def test_start_registers_active_scheduler_and_stop_clears(
    config: PanelAppDataConfigModel,
) -> None:
    # A long interval guarantees the loop sleeps; we cancel promptly via stop().
    sched = RefreshScheduler(config, interval_seconds=999.0, jitter_seconds=0.0)
    await sched.start()
    try:
        assert get_active_scheduler() is sched
        # start() is idempotent.
        await sched.start()
    finally:
        await sched.stop()
    assert get_active_scheduler() is None
    # stop() is safe to call again.
    await sched.stop()


def test_reload_is_defensive_against_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # _reload swallows any failure in the adapter import/reset path.
    import importlib

    def explode(_name: str) -> Any:
        raise RuntimeError("no module")

    monkeypatch.setattr(importlib, "import_module", explode)
    RefreshScheduler._reload()  # must not raise
