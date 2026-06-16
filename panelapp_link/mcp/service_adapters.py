"""Service binding for MCP tools.

Single place where the live :class:`PanelAppService` is constructed for tool use.
Tools call :func:`get_panelapp_service`. The service is a process-wide singleton
built over one shared :class:`PanelAppRestClient` (an async httpx client), created
lazily on first use. There is no database and no hot-reload: the service is pure
live-API with an in-memory TTL cache.

Tests inject a service via :func:`set_service_for_testing`;
:func:`reset_panelapp_service` drops the singleton and closes the owned client.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from panelapp_link.config import get_data_config
from panelapp_link.services.panelapp_service import PanelAppService

if TYPE_CHECKING:
    from panelapp_link.api.client import PanelAppRestClient

_OVERRIDE: PanelAppService | None = None
_CACHED: PanelAppService | None = None
_CLIENT: PanelAppRestClient | None = None
# Holds a strong reference to an in-flight best-effort close task so it is not
# garbage-collected before it runs (see RUF006).
_CLOSE_TASK: asyncio.Task[None] | None = None


def get_panelapp_service() -> PanelAppService:
    """Return the shared live PanelAppService, building it on first use.

    Constructs a single shared :class:`PanelAppRestClient` over the active data
    config and wraps it in a cached :class:`PanelAppService`. Never touches the
    network at construction time -- requests only happen when a tool calls a
    service method.
    """
    global _CACHED, _CLIENT
    if _OVERRIDE is not None:
        return _OVERRIDE
    if _CACHED is not None:
        return _CACHED

    from panelapp_link.api.client import PanelAppRestClient

    cfg = get_data_config()
    _CLIENT = PanelAppRestClient(cfg)
    _CACHED = PanelAppService(_CLIENT, cfg, cache_ttl=cfg.cache_ttl, cache_size=cfg.cache_size)
    return _CACHED


def set_service_for_testing(service: PanelAppService | None) -> None:
    """Inject (or clear) a service instance for tests."""
    global _OVERRIDE
    _OVERRIDE = service


def reset_panelapp_service() -> None:
    """Drop the cached service and close the owned client, if any."""
    global _CACHED, _CLIENT
    client = _CLIENT
    _CACHED = None
    _CLIENT = None
    if client is not None:
        _close_client(client)


def _close_client(client: PanelAppRestClient) -> None:
    """Best-effort async close of a client from a sync context."""
    global _CLOSE_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(client.aclose())
        return
    _CLOSE_TASK = loop.create_task(client.aclose())
