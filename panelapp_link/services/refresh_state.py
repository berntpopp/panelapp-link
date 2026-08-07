"""Synchronized state for atomic PanelApp panel-list refreshes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

RefreshStatus = Literal["disabled", "initializing", "healthy", "degraded", "stale"]


def _utc_rfc3339(value: datetime) -> str:
    """Render an aware wall-clock value as RFC 3339 UTC."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PanelListGeneration:
    """One complete, atomically published generation of PanelApp list data."""

    panels: Mapping[str, tuple[dict[str, Any], ...]]
    signed_off: Mapping[str, Mapping[int, dict[str, Any]]]
    created_at: str
    created_monotonic: float
    expires_monotonic: float


@dataclass(frozen=True)
class RefreshSnapshot:
    """Immutable caller-visible refresh state."""

    enabled: bool
    interval_seconds: int
    last_attempt_at: str | None
    last_successful_refresh_at: str | None
    age_seconds: float | None
    consecutive_failures: int
    failures_total: int
    status: RefreshStatus
    last_error_type: str | None

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-ready freshness contract."""
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "last_attempt_at": self.last_attempt_at,
            "last_successful_refresh_at": self.last_successful_refresh_at,
            "age_seconds": self.age_seconds,
            "consecutive_failures": self.consecutive_failures,
            "failures_total": self.failures_total,
            "status": self.status,
            "last_error_type": self.last_error_type,
        }


class RefreshState:
    """Own refresh counters and clocks without knowing how data is acquired."""

    def __init__(
        self,
        *,
        interval_seconds: int,
        cache_ttl_seconds: int,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._last_attempt_at: str | None = None
        self._last_successful_refresh_at: str | None = None
        self._last_success_monotonic: float | None = None
        self._consecutive_failures = 0
        self._failures_total = 0
        self._last_error_type: str | None = None
        self._escalation_logged = False

    def record_attempt(self) -> None:
        """Record the start of one complete generation acquisition."""
        with self._lock:
            self._last_attempt_at = _utc_rfc3339(self._wall_clock())

    def record_success(self) -> None:
        """Record a complete acquisition and log recovery once when applicable."""
        with self._lock:
            prior_status, _age = self._status_and_age(self._monotonic())
            now_wall = self._wall_clock()
            now_monotonic = self._monotonic()
            self._last_successful_refresh_at = _utc_rfc3339(now_wall)
            self._last_success_monotonic = now_monotonic
            self._consecutive_failures = 0
            self._last_error_type = None
            self._escalation_logged = False
            if prior_status in {"degraded", "stale"}:
                logger.info("panelapp refresh recovered")

    def record_failure(self, error: type[BaseException] | BaseException) -> None:
        """Record one failed acquisition using only its safe exception class name."""
        error_type = error.__name__ if isinstance(error, type) else error.__class__.__name__
        with self._lock:
            self._consecutive_failures += 1
            self._failures_total += 1
            self._last_error_type = error_type
            logger.warning("panelapp refresh failed: %s", error_type)
            self._log_escalation_if_needed(self._monotonic())

    def snapshot(self) -> RefreshSnapshot:
        """Return one immutable refresh-state snapshot."""
        with self._lock:
            now = self._monotonic()
            status, age = self._status_and_age(now)
            self._log_escalation_if_needed(now)
            return RefreshSnapshot(
                enabled=self._interval_seconds > 0,
                interval_seconds=self._interval_seconds,
                last_attempt_at=self._last_attempt_at,
                last_successful_refresh_at=self._last_successful_refresh_at,
                age_seconds=age,
                consecutive_failures=self._consecutive_failures,
                failures_total=self._failures_total,
                status=status,
                last_error_type=self._last_error_type,
            )

    def _status_and_age(self, now: float) -> tuple[RefreshStatus, float | None]:
        age = (
            max(0.0, round(now - self._last_success_monotonic, 3))
            if self._last_success_monotonic is not None
            else None
        )
        if self._interval_seconds <= 0:
            return "disabled", age
        if self._last_success_monotonic is None and not self._consecutive_failures:
            return "initializing", age
        stale_after = max(2 * self._interval_seconds, self._cache_ttl_seconds)
        if self._consecutive_failures >= 3 or (age is not None and age >= stale_after):
            return "stale", age
        if self._consecutive_failures:
            return "degraded", age
        return "healthy", age

    def _log_escalation_if_needed(self, now: float) -> None:
        status, _age = self._status_and_age(now)
        if status == "stale" and not self._escalation_logged:
            logger.error(
                "panelapp refresh stale after %d consecutive failures: %s",
                self._consecutive_failures,
                self._last_error_type or "none",
            )
            self._escalation_logged = True
