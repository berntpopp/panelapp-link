"""Deterministic tests for panel-list refresh state and generations."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from panelapp_link.exceptions import DownloadError
from panelapp_link.services.refresh_state import PanelListGeneration, RefreshState


class FakeClocks:
    def __init__(self) -> None:
        self.wall_now = datetime(2026, 8, 7, 10, 30, tzinfo=UTC)
        self.monotonic_now = 100.0

    def wall(self) -> datetime:
        return self.wall_now

    def monotonic(self) -> float:
        return self.monotonic_now

    def advance(self, seconds: float) -> None:
        self.wall_now += timedelta(seconds=seconds)
        self.monotonic_now += seconds


def _state(clocks: FakeClocks, *, interval: int = 60, ttl: int = 600) -> RefreshState:
    return RefreshState(
        interval_seconds=interval,
        cache_ttl_seconds=ttl,
        wall_clock=clocks.wall,
        monotonic=clocks.monotonic,
    )


def test_refresh_state_tracks_attempt_success_and_failure() -> None:
    clocks = FakeClocks()
    state = _state(clocks)

    assert state.snapshot().status == "initializing"
    state.record_attempt()
    state.record_success()
    assert state.snapshot().status == "healthy"

    state.record_attempt()
    state.record_failure(DownloadError)
    snapshot = state.snapshot()
    assert snapshot.status == "degraded"
    assert snapshot.consecutive_failures == 1
    assert snapshot.failures_total == 1
    assert snapshot.last_error_type == "DownloadError"


def test_disabled_state_has_exact_public_snapshot_keys() -> None:
    clocks = FakeClocks()
    snapshot = _state(clocks, interval=0).snapshot()

    assert snapshot.status == "disabled"
    assert snapshot.to_dict() == {
        "enabled": False,
        "interval_seconds": 0,
        "last_attempt_at": None,
        "last_successful_refresh_at": None,
        "age_seconds": None,
        "consecutive_failures": 0,
        "failures_total": 0,
        "status": "disabled",
        "last_error_type": None,
    }


def test_three_failures_escalate_once_to_error(caplog: pytest.LogCaptureFixture) -> None:
    clocks = FakeClocks()
    state = _state(clocks)
    state.record_attempt()
    state.record_success()

    with caplog.at_level(logging.INFO, logger="panelapp_link.services.refresh_state"):
        for _ in range(5):
            state.record_attempt()
            state.record_failure(DownloadError)

    assert state.snapshot().status == "stale"
    error_records = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(error_records) == 1
    assert "DownloadError" in error_records[0].getMessage()


def test_first_cold_failure_is_degraded_and_success_logs_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clocks = FakeClocks()
    state = _state(clocks)
    state.record_attempt()
    state.record_failure(DownloadError)
    assert state.snapshot().status == "degraded"

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="panelapp_link.services.refresh_state"):
        state.record_attempt()
        state.record_success()

    assert (
        len(
            [
                record
                for record in caplog.records
                if record.levelno == logging.INFO and "recovered" in record.getMessage()
            ]
        )
        == 1
    )


def test_age_threshold_escalates_at_max_interval_or_ttl(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clocks = FakeClocks()
    state = _state(clocks, interval=400, ttl=600)
    state.record_attempt()
    state.record_success()
    clocks.advance(799)
    state.record_attempt()
    state.record_failure(DownloadError)
    assert state.snapshot().status == "degraded"

    clocks.advance(1)
    with caplog.at_level(logging.ERROR, logger="panelapp_link.services.refresh_state"):
        assert state.snapshot().status == "stale"
        assert state.snapshot().status == "stale"

    assert len([record for record in caplog.records if record.levelno == logging.ERROR]) == 1


def test_success_after_escalation_logs_recovery_and_resets_failure_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clocks = FakeClocks()
    state = _state(clocks)
    for _ in range(3):
        state.record_attempt()
        state.record_failure(DownloadError)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="panelapp_link.services.refresh_state"):
        state.record_attempt()
        state.record_success()

    snapshot = state.snapshot()
    assert snapshot.status == "healthy"
    assert snapshot.consecutive_failures == 0
    assert snapshot.last_error_type is None
    recovery_records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and "recovered" in record.getMessage()
    ]
    assert len(recovery_records) == 1


def test_timestamps_are_rfc3339_utc_and_age_uses_monotonic_clock() -> None:
    clocks = FakeClocks()
    state = _state(clocks)
    state.record_attempt()
    clocks.advance(2.25)
    state.record_success()
    clocks.wall_now -= timedelta(days=10)
    clocks.monotonic_now += 7.75

    snapshot = state.snapshot()
    assert snapshot.last_attempt_at == "2026-08-07T10:30:00Z"
    assert snapshot.last_successful_refresh_at == "2026-08-07T10:30:02.250000Z"
    assert snapshot.age_seconds == 7.75


def test_panel_list_generation_is_frozen() -> None:
    generation = PanelListGeneration(
        panels={"uk": ({"id": 1},), "australia": ({"id": 2},)},
        signed_off={"uk": {1: {"version": "1.0"}}, "australia": {}},
        created_at="2026-08-07T10:30:00Z",
        created_monotonic=100.0,
        expires_monotonic=700.0,
    )

    with pytest.raises(FrozenInstanceError):
        generation.created_at = "changed"  # type: ignore[misc]
