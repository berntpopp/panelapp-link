"""Service binding for MCP tools.

Single place where the repository + service are constructed for tool use. Tools
call :func:`get_panelapp_service`. The cached service **hot-reloads** when the
database file on disk changes (e.g. after a scheduled refresh or an external cron
rebuild atomically swaps the file): a cheap ``stat`` per call detects the new
file and reopens the read-only connection.

On a missing/unbuilt database the underlying :class:`PanelAppRepository`
constructor raises :class:`~panelapp_link.exceptions.DataUnavailableError`; that
propagates out of ``get_panelapp_service`` so the calling tool's ``run_mcp_tool``
boundary converts it into a ``data_unavailable`` envelope. Tests inject a service
via :func:`set_service_for_testing`.
"""

from __future__ import annotations

from pathlib import Path

from panelapp_link.config import get_data_config
from panelapp_link.services.panelapp_service import PanelAppService

_OVERRIDE: PanelAppService | None = None
_CACHED: PanelAppService | None = None
_CACHED_MTIME: float | None = None
_REPO: object | None = None  # PanelAppRepository; typed loosely to avoid import cycle


def _db_mtime(path: Path) -> float | None:
    """Return the database file's mtime, or ``None`` when it does not exist."""
    try:
        return path.stat().st_mtime_ns / 1_000_000_000
    except FileNotFoundError:
        return None


def get_panelapp_service() -> PanelAppService:
    """Return the shared PanelAppService, reopening the database when it changes.

    Reopens the underlying read-only connection when the database file's mtime
    changes, so a refresh that atomically swaps ``panelapp.sqlite`` is picked up
    live. Raises :class:`DataUnavailableError` (via the repository constructor)
    when the database file is absent.
    """
    global _CACHED, _CACHED_MTIME, _REPO
    if _OVERRIDE is not None:
        return _OVERRIDE

    cfg = get_data_config()
    current_mtime = _db_mtime(cfg.db_path)
    if _CACHED is not None and current_mtime is not None and current_mtime == _CACHED_MTIME:
        return _CACHED

    # Imported lazily so capabilities/import paths don't pull the data layer
    # until a tool actually needs it.
    from panelapp_link.data.repository import PanelAppRepository

    _close_repo()
    repo = PanelAppRepository(cfg.db_path)
    _REPO = repo
    _CACHED = PanelAppService(repo, cache_size=cfg.cache_size, cache_ttl=cfg.cache_ttl)
    _CACHED_MTIME = _db_mtime(cfg.db_path)
    return _CACHED


def _close_repo() -> None:
    """Close and drop the cached repository/service, if any."""
    global _CACHED, _CACHED_MTIME, _REPO
    if _REPO is not None:
        close = getattr(_REPO, "close", None)
        if callable(close):
            close()
    _REPO = None
    _CACHED = None
    _CACHED_MTIME = None


def set_service_for_testing(service: PanelAppService | None) -> None:
    """Inject (or clear) a service instance for tests."""
    global _OVERRIDE
    _OVERRIDE = service


def reset_panelapp_service() -> None:
    """Clear the cached service so the next call reopens the database."""
    _close_repo()
