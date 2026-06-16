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
            "get_server_capabilities": "n/a",
            "get_panelapp_diagnostics": "n/a",
        },
        "recommended_workflows": [
            "panel text -> search_panels -> get_panel(panel_id, region) -> "
            "get_panel_genes(panel_id, region, min_confidence='green')",
            "gene symbol -> resolve_gene -> get_gene_panels(gene_symbol=... or hgnc_id=...)",
            "green diagnostic-grade genes on a panel -> "
            "get_panel_genes(panel_id=..., region=..., min_confidence='green')",
            "compare a gene across regions -> get_gene_panels(hgnc_id=..., region='both')",
        ],
        "parameter_conventions": {
            "region": "uk (Genomics England) | australia | both (default). get_panel "
            "requires a single concrete region (uk or australia), not both.",
            "gene_symbol": "approved gene symbol (e.g. BRCA1); mutually exclusive with hgnc_id",
            "hgnc_id": "HGNC CURIE (e.g. HGNC:1100); mutually exclusive with gene_symbol",
            "panel_id": "integer PanelApp panel id within a region",
            "entity_type": "gene | region | str | all (get_panel_genes; default gene)",
            "min_confidence": "green | amber | red; filters entities by rank "
            "(green = green only; amber = amber+green; red = all)",
            "response_mode": "minimal | compact | standard | full (default compact)",
            "cursor": "opaque, build-bound page token from a prior truncated.next_cursor; "
            "rejected (invalid_input, field=cursor) if the database was refreshed since.",
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
                "code": "ambiguous_query",
                "operational_only": False,
                "when": "resolve_gene free text matches multiple genes",
            },
            {
                "code": "data_unavailable",
                "operational_only": True,
                "when": "database not built (ingest/ops); not reachable from a well-formed query",
            },
            {
                "code": "upstream_unavailable",
                "operational_only": True,
                "when": "PanelApp API download failed (ingest/ops)",
            },
            {
                "code": "rate_limited",
                "operational_only": True,
                "when": "PanelApp API rate-limited the crawl (ingest/ops)",
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
            "ambiguous_query",
            "data_unavailable",
            "upstream_unavailable",
            "rate_limited",
            "internal_error",
        ],
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
    """Best-effort data provenance; never raises (capabilities must always work).

    Uses a defensive lazy import + broad except so capabilities import and tests
    work even when ``data/repository.py`` (built in parallel) or the SQLite DB is
    absent or unreadable.
    """
    try:
        from panelapp_link.config import get_data_config
        from panelapp_link.data.repository import PanelAppRepository

        repo = PanelAppRepository(get_data_config().db_path)
        meta = repo.get_meta()
        status: dict[str, Any] = {"status": "ready"}
        meta_dict = dict(meta)
        for key in (
            "schema_version",
            "uk_panel_count",
            "au_panel_count",
            "entity_count",
            "gene_count",
            "build_utc",
        ):
            if key in meta_dict:
                status[key] = meta_dict[key]
        return status
    except Exception:  # ImportError, missing DB, unreadable -- all degrade to unavailable
        return {"status": "data_unavailable"}


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
