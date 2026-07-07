"""Configuration management for PanelApp-Link.

Settings load from environment variables prefixed ``PANELAPP_LINK_`` (nested
config via ``__``, e.g. ``PANELAPP_LINK_DATA__UK_API_URL``) and an optional
``.env`` file.

The service is a pure live-API client: there is no local database or ingest, so
the data config only describes the upstream APIs, the HTTP client, and the
in-memory per-query cache.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from panelapp_link import __version__


class PanelAppDataConfigModel(BaseModel):
    """PanelApp live-API source + in-memory cache configuration."""

    uk_api_url: str = Field(
        default="https://panelapp.genomicsengland.co.uk/api/v1",
        description="Base URL for the Genomics England PanelApp (UK) REST API.",
    )
    au_api_url: str = Field(
        default="https://panelapp-aus.org/api/v1",
        description="Base URL for the PanelApp Australia REST API.",
    )
    request_timeout: int = Field(
        default=60,
        ge=5,
        le=900,
        description="HTTP timeout (seconds) for each PanelApp API request.",
    )
    max_concurrency: int = Field(
        default=4,
        ge=1,
        le=64,
        description=(
            "Max concurrent API requests (semaphore size). Kept low by default "
            "because PanelApp rate-limits aggressive per-IP request bursts."
        ),
    )
    max_retries: int = Field(
        default=5,
        ge=0,
        le=10,
        description="Max retries on retryable API responses (429/5xx/timeout).",
    )
    user_agent: str = Field(
        default=f"PanelApp-Link/{__version__} (+https://github.com/berntpopp/panelapp-link)",
        description="User-Agent sent to the PanelApp APIs.",
    )
    cache_size: int = Field(
        default=512,
        ge=0,
        le=8192,
        description="Max entries in the in-process query cache (0 disables).",
    )
    cache_ttl: int = Field(
        default=21600,
        ge=0,
        le=86400,
        description="In-memory cache TTL in seconds (default 6 hours).",
    )
    prewarm: bool = Field(
        default=False,
        description=(
            "On server start (HTTP/unified transports), pre-fetch the heavy panel "
            "list + signed-off list for both regions so the first search_panels is "
            "warm. Off by default to preserve the stateless no-boot-network posture; "
            "single-flight + the TTL cache still pay the cold double-fetch at most "
            "once per process even when this is off."
        ),
    )
    refresh_interval: int = Field(
        default=0,
        ge=0,
        le=86400,
        description=(
            "Seconds between background refreshes of the panel + signed-off lists "
            "(stale-while-revalidate). 0 disables the background task."
        ),
    )
    gene_batch_cap: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max gene symbols per get_panels_for_genes call (upstream politeness).",
    )


class OtelConfigModel(BaseModel):
    """OpenTelemetry tracing toggle (opt-in; default no-op)."""

    enabled: bool = Field(default=False, description="Install an OTLP TracerProvider on startup.")
    console: bool = Field(
        default=False,
        description="Also export spans to stderr (dev only; disabled under stdio transport).",
    )


class ServerSettings(BaseSettings):
    """Top-level server configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        env_prefix="PANELAPP_LINK_",
    )

    # Server
    host: str = Field(default="127.0.0.1", description="Server host")
    port: int = Field(default=8000, ge=1024, le=65535, description="Server port")
    reload: bool = Field(default=False, description="Enable auto-reload in development")

    # Transport
    transport: Literal["unified", "http", "stdio"] = Field(
        default="unified", description="Server transport mode"
    )
    mcp_path: str = Field(default="/mcp", description="MCP endpoint path")
    mcp_rate_limit_per_minute: int = Field(
        default=0,
        ge=0,
        le=100000,
        description=(
            "Per-process MCP tool-call rate cap (token bucket). 0 disables. When "
            "set, calls over the cap are rejected with a structured rate_limited "
            "envelope and never reach the upstream PanelApp APIs -- a politeness "
            "guard for unauthenticated public hosting."
        ),
    )

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins",
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description=(
            "Allow CORS credentials. Off by default: this backend is "
            "unauthenticated and holds no cookies/session, so credentialed CORS "
            "is meaningless. Enabling it together with a '*' origin is rejected "
            "at startup."
        ),
    )
    cors_allow_methods: list[str] = Field(default=["GET", "POST", "OPTIONS"])
    cors_allow_headers: list[str] = Field(default=["*"])

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="console")

    # Data
    data: PanelAppDataConfigModel = Field(
        default_factory=PanelAppDataConfigModel,
        description="PanelApp live-API source + cache configuration",
    )

    # Observability
    otel: OtelConfigModel = Field(
        default_factory=OtelConfigModel, description="OpenTelemetry tracing configuration"
    )

    @field_validator("mcp_path")
    @classmethod
    def _validate_mcp_path(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return list(v) if v else []


# Global settings instance
settings = ServerSettings()


def get_data_config() -> PanelAppDataConfigModel:
    """Return the PanelApp data configuration from global settings."""
    return settings.data
