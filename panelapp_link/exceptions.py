"""Typed exceptions for PanelApp-Link.

The MCP envelope (`panelapp_link.mcp.envelope`) maps these onto stable error
codes, so the hierarchy here mirrors the error taxonomy advertised in
capabilities. ``McpToolError`` is intentionally NOT defined here -- it lives in
the envelope module alongside the classifier that consumes it.
"""

from __future__ import annotations


class PanelAppError(Exception):
    """Base exception for all PanelApp-Link errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class InvalidInputError(PanelAppError):
    """Raised when caller input fails validation."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class NotFoundError(PanelAppError):
    """Raised when a requested panel, gene, or entity does not exist."""


class DownloadError(PanelAppError):
    """Raised when PanelApp data cannot be fetched from the upstream API."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(DownloadError):
    """Raised when the PanelApp API rate-limits or rejects requests (403/429)."""


class DisallowedURLError(PanelAppError):
    """Raised when an outbound request or redirect targets a non-allowlisted URL.

    NON-RETRYABLE: retrying re-issues the identical blocked request. It
    deliberately does NOT subclass :class:`DownloadError`, so the envelope never
    misclassifies it as a retryable ``upstream_unavailable`` and the client's
    retry loop (which only catches httpx transport errors) never swallows it.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__("Outbound HTTP policy rejected the request.")


class ResponseTooLargeError(DownloadError):
    """Raised when a decoded upstream response crosses the HTTP-policy cap.

    This is a policy failure, not a transient upstream failure, and must never
    enter the retry loop.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__("Outbound HTTP policy rejected the request.")
