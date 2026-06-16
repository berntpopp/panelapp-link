"""Configuration management for PanelApp-Link.

Settings load from environment variables prefixed ``PANELAPP_LINK_`` (nested
config via ``__``, e.g. ``PANELAPP_LINK_DATA__DATA_DIR``) and an optional
``.env`` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from panelapp_link import __version__

# Project root: <repo>/panelapp_link/config.py -> <repo>
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"


class PanelAppDataConfigModel(BaseModel):
    """PanelApp data source + local store configuration."""

    uk_api_url: str = Field(
        default="https://panelapp.genomicsengland.co.uk/api/v1",
        description="Base URL for the Genomics England PanelApp (UK) REST API.",
    )
    au_api_url: str = Field(
        default="https://panelapp-aus.org/api/v1",
        description="Base URL for the PanelApp Australia REST API.",
    )
    data_dir: Path = Field(
        default=_DEFAULT_DATA_DIR,
        description="Directory holding the built SQLite database and crawl cache.",
    )
    db_filename: str = Field(
        default="panelapp.sqlite",
        description="SQLite database filename within data_dir.",
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
            "Max concurrent API requests during a crawl (semaphore size). Kept "
            "low by default because PanelApp rate-limits aggressive per-IP crawls."
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
    auto_bootstrap: bool = Field(
        default=True,
        description="Build the database on first use by crawling the APIs if absent.",
    )
    refresh_enabled: bool = Field(
        default=True,
        description=(
            "Run an in-process scheduler (unified/http transports only) that "
            "conditionally refreshes the database on an interval. Disable when an "
            "external scheduler (cron sidecar, k8s CronJob) owns refresh."
        ),
    )
    refresh_interval_hours: float = Field(
        default=24.0,
        ge=1.0,
        le=720.0,
        description=(
            "Hours between conditional refresh checks. PanelApp panels change "
            "incrementally; refresh re-lists and re-fetches only changed panels."
        ),
    )
    refresh_jitter_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="Random jitter added to each refresh to avoid thundering herds.",
    )
    build_lock_timeout: int = Field(
        default=600,
        ge=1,
        le=3600,
        description="Seconds to wait for the cross-process build lock before giving up.",
    )
    cache_size: int = Field(
        default=512,
        ge=0,
        le=8192,
        description="Max entries in the in-process query cache (0 disables).",
    )
    cache_ttl: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description="Query cache TTL in seconds.",
    )

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.data_dir / self.db_filename

    @field_validator("data_dir")
    @classmethod
    def _expand_data_dir(cls, v: Path) -> Path:
        return Path(v).expanduser()


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

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins",
    )
    cors_allow_credentials: bool = Field(default=True, description="Allow CORS credentials")
    cors_allow_methods: list[str] = Field(default=["GET", "POST", "OPTIONS"])
    cors_allow_headers: list[str] = Field(default=["*"])

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="console")

    # Data
    data: PanelAppDataConfigModel = Field(
        default_factory=PanelAppDataConfigModel,
        description="PanelApp data source + store configuration",
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
