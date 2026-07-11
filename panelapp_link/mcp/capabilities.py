"""Capabilities discovery surface for PanelApp-Link (parity with sibling -link servers)."""

from __future__ import annotations

import functools
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from panelapp_link.constants import (
    CONFIDENCE_RANK,
    DATA_LICENSE,
)
from panelapp_link.mcp.resources import (
    PANELAPP_LICENSE_NOTE,
    PANELAPP_REFERENCE_NOTES,
    PANELAPP_USAGE_NOTES,
    RECOMMENDED_CITATION,
    RESEARCH_USE_NOTICE,
)
from panelapp_link.models.enums import (
    CONFIDENCE_LABELS,
    ENTITY_TYPES,
    REGIONS,
    RESPONSE_MODES,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

TOOLS: tuple[str, ...] = (
    "search_panels",
    "get_panel",
    "get_panel_genes",
    "get_gene_panels",
    "resolve_gene",
    "compare_panels",
    "get_panels_for_genes",
    "get_server_capabilities",
    "get_panelapp_diagnostics",
)


def _server_version() -> str:
    try:
        return version("panelapp-link")
    except PackageNotFoundError:
        return "0.0.0"


@functools.cache
def _static_surface() -> dict[str, Any]:
    surface: dict[str, Any] = {
        "server": "panelapp-link",
        "server_version": _server_version(),
        "mcp_protocol_version": "2025-11-25",
        "data_source": "PanelApp -- Genomics England (UK) and PanelApp Australia",
        "research_use_only": True,
        "data_license": DATA_LICENSE,
        "tools": list(TOOLS),
        "vocabulary": {
            "confidence_labels": [
                {"label": label, "rank": CONFIDENCE_RANK[label]} for label in CONFIDENCE_LABELS
            ],
            "entity_types": list(ENTITY_TYPES),
            "regions": list(REGIONS),
        },
        "response_modes": {
            "minimal": "ids + name + counts only",
            "compact": "default; key fields (panel summary; entity symbol/confidence/moi)",
            "standard": "adds phenotypes, penetrance, signed-off detail, region coords summary",
            "full": "adds evidence, publications, omim, tags, and raw entity extras",
        },
        "tool_defaults": {
            "search_panels": "compact",
            "get_panel": "compact",
            "get_panel_genes": "compact",
            "get_gene_panels": "compact",
            "resolve_gene": "compact",
            "compare_panels": "compact",
            "get_panels_for_genes": "compact",
            "get_server_capabilities": "n/a",
            "get_panelapp_diagnostics": "n/a",
        },
        "recommended_workflows": [
            "panel text -> search_panels -> get_panel(panel_id, region) -> "
            "get_panel_genes(panel_id, region, min_confidence='green')",
            "gene symbol -> resolve_gene -> get_gene_panels(gene_symbol=...)",
            "green diagnostic-grade genes on a panel -> "
            "get_panel_genes(panel_id=..., region=..., min_confidence='green')",
            "compare a gene across regions -> get_gene_panels(gene_symbol=..., region='both')",
            "compare two panels' genes -> compare_panels(panels=[{panel_id, region}, ...])",
            "triage a gene list -> get_panels_for_genes(gene_symbols=[...], min_confidence='green')",
        ],
        "parameter_conventions": {
            "region": "uk (Genomics England) | australia | both (default). get_panel "
            "requires a single concrete region (uk or australia), not both.",
            "gene_symbol": "approved gene symbol (e.g. BRCA1); the query key for "
            "resolve_gene / get_gene_panels",
            "hgnc_id": "HGNC CURIE (e.g. HGNC:1100); OPTIONAL disambiguation filter "
            "for get_gene_panels results -- gene_symbol drives the query",
            "panel_id": "integer PanelApp panel id within a region",
            "entity_type": "gene | region | str | all (get_panel_genes; default gene)",
            "min_confidence": "green | amber | red; filters entities by rank "
            "(green = green only; amber = amber+green; red = all)",
            "response_mode": "minimal | compact | standard | full (default compact)",
            "cursor": "opaque offset-based page token from a prior truncated.next_cursor; "
            "pass it to continue paging. Rejected (invalid_input, field=cursor) only if "
            "malformed.",
        },
        "error_codes": [
            {
                "code": "invalid_input",
                "operational_only": False,
                "when": "malformed/out-of-vocab argument; carries field_errors",
            },
            {
                "code": "not_found",
                "operational_only": False,
                "when": "a well-formed panel id / gene resolves to nothing",
            },
            {
                "code": "upstream_unavailable",
                "operational_only": True,
                "when": "a PanelApp API request failed (network or 5xx)",
            },
            {
                "code": "rate_limited",
                "operational_only": True,
                "when": "PanelApp rate-limited the request (HTTP 429)",
            },
            {
                "code": "limit_exceeded",
                "operational_only": False,
                "when": "a response exceeds a v1.1 untrusted-text ceiling "
                "(object count / bytes); narrow the request",
            },
            {
                "code": "internal_error",
                "operational_only": True,
                "when": "unexpected server fault",
            },
        ],
        "error_codes_list": [
            "invalid_input",
            "not_found",
            "upstream_unavailable",
            "rate_limited",
            "limit_exceeded",
            "internal_error",
        ],
        "observability": {
            "metrics_endpoint": "GET /metrics (Prometheus text 0.0.4; HTTP/unified transport)",
            "per_call_meta": [
                "request_id (12-hex correlation id; also an OpenTelemetry span attribute)",
                "elapsed_ms (server-side wall-clock)",
                "cache (hit|miss|coalesced|partial)",
                "upstream_ms + upstream{region:{calls,ms}} (per-region upstream timing)",
            ],
            "tracing": (
                "OpenTelemetry spans wrap each tool call and each upstream region "
                "fetch (one trace per request, correlated by request_id); activate "
                "by configuring an OTel SDK + exporter."
            ),
            "metrics": (
                "RED aggregates (request rate, errors by code, tool + per-region "
                "upstream duration p50/p95/p99, cache hit ratio) via "
                "get_panelapp_diagnostics and GET /metrics."
            ),
        },
        "resources": {
            "panelapp://capabilities": "this document",
            "panelapp://usage": "compact usage notes",
            "panelapp://reference": "confidence labels/ranks, entity types, regions, paging",
            "panelapp://license": "PanelApp content terms + research-use note",
            "panelapp://citation": "recommended citations (UK + Australia)",
            "panelapp://research-use": "research-use-only notice",
        },
        "response_modes_list": list(RESPONSE_MODES),
        "research_use_notice": RESEARCH_USE_NOTICE,
        "license_note": PANELAPP_LICENSE_NOTE,
        "usage_notes": PANELAPP_USAGE_NOTES,
        "reference_notes": PANELAPP_REFERENCE_NOTES,
        "citation": RECOMMENDED_CITATION,
    }
    digest = hashlib.sha256(
        json.dumps(surface, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    surface["capabilities_version"] = digest
    return surface


def capabilities_version() -> str:
    """16-char content hash of the static capabilities surface."""
    return str(_static_surface()["capabilities_version"])


def server_version() -> str:
    """Installed package version (mirrors the capabilities surface)."""
    return str(_static_surface()["server_version"])


def build_capabilities() -> dict[str, Any]:
    """Return the capabilities document, including live data freshness if built.

    The live ``data`` block lives outside the hashed static surface, so it never
    perturbs ``capabilities_version``.
    """
    surface = dict(_static_surface())
    surface["data"] = _data_status()
    return surface


def _data_status() -> dict[str, Any]:
    """Return the live backend status block (mode, sources, cache TTL).

    The service is pure live-API with no local database, so this reports the
    upstream PanelApp source URLs and the in-memory cache TTL. It never raises.
    """
    from panelapp_link.config import get_data_config

    cfg = get_data_config()
    return {
        "mode": "live",
        "sources": {"uk": cfg.uk_api_url, "australia": cfg.au_api_url},
        "cache_ttl_seconds": cfg.cache_ttl,
    }


def register_capability_resources(mcp: FastMCP) -> None:
    """Register the panelapp:// resource family."""

    @mcp.resource("panelapp://capabilities", mime_type="application/json")
    def capabilities() -> str:
        return json.dumps(build_capabilities())

    @mcp.resource("panelapp://usage", mime_type="text/plain")
    def usage() -> str:
        return PANELAPP_USAGE_NOTES

    @mcp.resource("panelapp://reference", mime_type="text/plain")
    def reference() -> str:
        return PANELAPP_REFERENCE_NOTES

    @mcp.resource("panelapp://license", mime_type="text/plain")
    def license_() -> str:
        return PANELAPP_LICENSE_NOTE

    @mcp.resource("panelapp://citation", mime_type="text/plain")
    def citation() -> str:
        return RECOMMENDED_CITATION

    @mcp.resource("panelapp://research-use", mime_type="text/plain")
    def research_use() -> str:
        return RESEARCH_USE_NOTICE
