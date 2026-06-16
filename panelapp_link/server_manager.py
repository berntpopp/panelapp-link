"""Unified server manager for HTTP, stdio, and unified (HTTP+MCP) transports."""

from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from panelapp_link import __version__
from panelapp_link.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from structlog.typing import FilteringBoundLogger


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: best-effort bootstrap, then the refresh scheduler.

    The startup build is non-fatal: if the database cannot be built (e.g. no
    network in CI, or an external scheduler owns it and has not run yet), the
    server still starts and tools report ``data_unavailable`` until data lands.
    The in-process scheduler keeps the database fresh and hot-reloads it on
    change. App *construction* never touches the network or the database — that
    only happens here, when uvicorn actually serves the app.
    """
    from panelapp_link.config import get_data_config
    from panelapp_link.ingest.builder import refresh as build_refresh
    from panelapp_link.logging_config import configure_logging
    from panelapp_link.services.refresh import build_scheduler

    logger = configure_logging()
    logger.info("panelapp-link starting", host=settings.host, port=settings.port)

    cfg = get_data_config()
    if cfg.auto_bootstrap and not cfg.db_path.exists():
        try:
            await build_refresh(cfg, force=False)
        except Exception as exc:  # non-fatal: serve, report data_unavailable until ready
            logger.warning("database not ready at startup", error=str(exc))

    scheduler = build_scheduler(cfg, logger)
    if scheduler is not None:
        await scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        logger.info("panelapp-link shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application (health + discovery surface).

    Safe to call with no database present and without any network access; the
    lifespan (bootstrap + scheduler) only runs when the app is actually served.
    """
    app = FastAPI(
        title="PanelApp-Link",
        description="MCP/API server for PanelApp gene-panel data (UK + Australia)",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    @app.get("/health")
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Liveness probe. Reports data status without forcing a build."""
        from panelapp_link.mcp.capabilities import _data_status

        return {"status": "ok", "version": __version__, "data": _data_status()}

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Service metadata."""
        return {
            "name": "PanelApp-Link",
            "version": __version__,
            "description": "MCP/API server for PanelApp gene-panel data (UK + Australia)",
            "docs": "/docs",
            "health": "/health",
            "mcp_endpoint": settings.mcp_path,
        }

    return app


class UnifiedServerManager:
    """Orchestrate startup of PanelApp-Link in any transport mode."""

    def __init__(self, logger: FilteringBoundLogger | None = None) -> None:
        self.logger = logger
        self._uvicorn_server: uvicorn.Server | None = None

    # --- Transports -----------------------------------------------------

    def build_app(self) -> FastAPI:
        """Construct the FastAPI app (used by tests and HTTP transports)."""
        return create_app()

    async def start_unified_server(self, host: str, port: int) -> None:
        """Start FastAPI + MCP (streamable-http) on the same port.

        Uses ``mcp.http_app(path=...)`` (fastmcp 3.x) and composes its lifespan
        with the FastAPI lifespan so the MCP session manager starts and stops
        cleanly. See https://gofastmcp.com/integrations/fastapi.
        """
        if self.logger:
            self.logger.info(
                "Starting unified server", host=host, port=port, mcp_path=settings.mcp_path
            )

        from panelapp_link.mcp.facade import create_panelapp_mcp

        fastapi_app = create_app()
        mcp = create_panelapp_mcp()
        mcp_asgi = mcp.http_app(path=settings.mcp_path)

        original_lifespan = fastapi_app.router.lifespan_context

        @asynccontextmanager
        async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(original_lifespan(app))
                await stack.enter_async_context(mcp_asgi.router.lifespan_context(app))
                yield

        fastapi_app.router.lifespan_context = combined_lifespan
        fastapi_app.mount("/", mcp_asgi)

        config = uvicorn.Config(
            app=fastapi_app, host=host, port=port, log_config=None, lifespan="on"
        )
        self._uvicorn_server = uvicorn.Server(config)
        await self._uvicorn_server.serve()

    async def start_http_only_server(self, host: str, port: int) -> None:
        """Start FastAPI only (no MCP)."""
        if self.logger:
            self.logger.info("Starting HTTP-only server", host=host, port=port)

        fastapi_app = create_app()
        config = uvicorn.Config(
            app=fastapi_app, host=host, port=port, log_config=None, lifespan="on"
        )
        self._uvicorn_server = uvicorn.Server(config)
        await self._uvicorn_server.serve()

    async def start_stdio_server(self) -> None:
        """Start the FastMCP stdio transport (for Claude Desktop)."""
        self._configure_stdio_environment()
        if self.logger:
            self.logger.info("Starting stdio MCP server")
        from panelapp_link.mcp.facade import create_panelapp_mcp

        mcp = create_panelapp_mcp()
        # show_banner=False is critical: non-JSON bytes on stdout corrupt framing.
        await mcp.run_async(transport="stdio", show_banner=False)

    # --- Lifecycle ------------------------------------------------------

    async def shutdown(self) -> None:
        """Gracefully stop any running server."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self.logger:
            self.logger.info("Shutdown complete")

    # --- Helpers --------------------------------------------------------

    @staticmethod
    def _configure_stdio_environment() -> None:
        """Suppress non-JSON output that would corrupt stdio MCP framing."""
        env_defaults: dict[str, Any] = {
            "PYTHONUNBUFFERED": "1",
            "PANELAPP_LINK_TRANSPORT": "stdio",
            "FASTMCP_DISABLE_BANNER": "1",
            "FASTMCP_NO_BANNER": "1",
            "FASTMCP_QUIET": "1",
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            "TERM": "dumb",
            "PYTHONWARNINGS": "ignore",
        }
        for key, value in env_defaults.items():
            os.environ.setdefault(key, value)
