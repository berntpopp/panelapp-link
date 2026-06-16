"""In-process scheduled refresh of the PanelApp database.

A dependency-free asyncio loop that, on an interval (plus jitter), runs a
*conditional* refresh via :func:`panelapp_link.ingest.builder.refresh`. That
function lists both regions, compares panel versions against the stored
``panel_versions_json``, and only re-crawls + atomically rebuilds when something
changed. When the database is rebuilt the served service singleton is reset so
subsequent tool calls open the swapped file.

The first run is scheduled one interval *after* startup, because the container
entrypoint (or the lifespan bootstrap) already ensures fresh data at boot. Only
the unified/http transports start this scheduler; stdio is short-lived and does
not. This module is import-safe: no scheduler is started at import time.

For deployments that prefer a dedicated scheduler (cron sidecar, Kubernetes
CronJob, systemd timer), disable this via
``PANELAPP_LINK_DATA__REFRESH_ENABLED=false`` and run ``panelapp-link-data
refresh`` externally -- the server still hot-reloads the swapped database file.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from panelapp_link.exceptions import DownloadError

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger

    from panelapp_link.config import PanelAppDataConfigModel

# The currently active scheduler (for diagnostics), set on start, cleared on stop.
_ACTIVE: RefreshScheduler | None = None


def get_active_scheduler() -> RefreshScheduler | None:
    """Return the running scheduler, if any (used by diagnostics)."""
    return _ACTIVE


class RefreshScheduler:
    """Periodically run a conditional PanelApp refresh and hot-reload on change."""

    def __init__(
        self,
        config: PanelAppDataConfigModel,
        logger: FilteringBoundLogger | None = None,
        *,
        interval_seconds: float | None = None,
        jitter_seconds: float | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else config.refresh_interval_hours * 3600.0
        )
        self._jitter = (
            jitter_seconds if jitter_seconds is not None else float(config.refresh_jitter_seconds)
        )
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_build_utc: str | None = None
        self._status: dict[str, Any] = {
            "enabled": True,
            "interval_seconds": self._interval,
            "state": "pending",
            "last_checked_utc": None,
            "last_changed": False,
            "last_error": None,
        }

    @property
    def status(self) -> dict[str, Any]:
        """A snapshot of the scheduler's last refresh outcome (for diagnostics)."""
        return dict(self._status)

    async def start(self) -> None:
        """Start the background refresh loop (idempotent)."""
        global _ACTIVE
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="panelapp-refresh")
        _ACTIVE = self
        if self._logger:
            self._logger.info(
                "refresh scheduler started",
                interval_seconds=self._interval,
                jitter_seconds=self._jitter,
            )

    async def stop(self) -> None:
        """Stop the loop and wait for the task to finish."""
        global _ACTIVE
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if _ACTIVE is self:
            _ACTIVE = None
        if self._logger:
            self._logger.info("refresh scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            delay = self._interval + random.uniform(0, self._jitter)  # noqa: S311 - not crypto
            # Sleep, but wake immediately if stop() is called.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            if self._stop.is_set():
                return
            await self._run_once()

    async def _run_once(self) -> None:
        """Run one conditional refresh; hot-reload the service when it changed."""
        from panelapp_link.ingest.builder import refresh

        try:
            meta = await refresh(self._config, force=False)
        except DownloadError as exc:
            self._record(error=f"download: {exc}")
            if self._logger:
                self._logger.warning("refresh failed: download error", error=str(exc))
            return
        except Exception as exc:  # defensive: a refresh must never kill the loop
            self._record(error=f"{type(exc).__name__}: {exc}")
            if self._logger:
                self._logger.error("refresh failed", error=str(exc))
            return

        # The builder rebuilds in place when versions changed; a new build_utc
        # is our change signal (an unchanged source returns the prior provenance).
        changed = meta.build_utc != self._last_build_utc and self._last_build_utc is not None
        self._last_build_utc = meta.build_utc
        if changed:
            self._reload()
            if self._logger:
                self._logger.info("refresh applied: database rebuilt", build_utc=meta.build_utc)
        elif self._logger:
            self._logger.info("refresh check: source not modified")
        self._record(changed=changed, build_utc=meta.build_utc)

    @staticmethod
    def _reload() -> None:
        """Reset the served service singleton so it reopens the swapped database.

        Imported dynamically (and defensively) so this module stays decoupled
        from the MCP service-adapter layer, which owns the singleton lifecycle.
        """
        with contextlib.suppress(Exception):
            adapters = importlib.import_module("panelapp_link.mcp.service_adapters")
            adapters.reset_panelapp_service()

    def _record(
        self, *, changed: bool = False, build_utc: str | None = None, error: str | None = None
    ) -> None:
        self._status.update(
            {
                "state": "error" if error else "ok",
                "last_checked_utc": datetime.now(tz=UTC).isoformat(),
                "last_changed": changed,
                "last_error": error,
            }
        )
        if build_utc is not None:
            self._status["last_build_utc"] = build_utc


def build_scheduler(
    config: PanelAppDataConfigModel, logger: FilteringBoundLogger | None = None
) -> RefreshScheduler | None:
    """Return a scheduler when in-app refresh is enabled, else ``None``."""
    if not config.refresh_enabled:
        return None
    return RefreshScheduler(config, logger)
