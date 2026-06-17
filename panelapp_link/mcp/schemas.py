"""JSON Schema (2020-12) output schemas advertised by PanelApp-Link tools.

FastMCP already emits ``structuredContent`` for dict-returning tools, but with a
contentless ``{type: object, additionalProperties: true}`` schema. These schemas
give clients a real, conformant field glossary. ``additionalProperties: true`` and
``required: ["success"]`` keep every response_mode tier and error envelope valid
(per the MCP spec, the server MUST make structuredContent conform to outputSchema).
"""

from __future__ import annotations

from typing import Any

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_OBJ: dict[str, Any] = {"type": "object", "additionalProperties": True}
_OBJ_OR_NULL: dict[str, Any] = {"type": ["object", "null"], "additionalProperties": True}
_OBJ_ARRAY: dict[str, Any] = {"type": "array", "items": _OBJ}
_ARRAY: dict[str, Any] = {"type": "array"}

_NEXT_COMMANDS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"tool": _STR, "arguments": _OBJ},
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    },
}

_UPSTREAM = {
    "type": "object",
    "description": "Per-region upstream fetch timing for this call ({calls, ms}).",
    "additionalProperties": {
        "type": "object",
        "properties": {"calls": _INT, "ms": {"type": "number"}},
        "additionalProperties": True,
    },
}

_META = {
    "type": "object",
    "description": "Per-call envelope metadata.",
    "properties": {
        "request_id": _STR,
        "elapsed_ms": {"type": "number"},
        "response_mode": _STR,
        "data_license": _STR,
        "unsafe_for_clinical_use": _BOOL,
        "recommended_citation": _STR,
        "citation_ref": _STR,
        "citation_short": _STR,
        "next_commands": _NEXT_COMMANDS,
        "tool": _STR,
        # Observability breadcrumbs (why did this call take N ms?):
        "cache": {"type": "string", "enum": ["hit", "miss", "coalesced", "partial"]},
        "upstream_ms": {"type": "number"},
        "upstream": _UPSTREAM,
    },
    "additionalProperties": True,
}

_TRUNCATION = {
    "type": "object",
    "properties": {
        "total": _INT,
        "returned": _INT,
        "next_offset": _INT,
        "next_cursor": _STR,
        "hint": _STR,
    },
    "additionalProperties": True,
}

# Fields shared by success and error envelopes (all optional but ``success``).
_BASE_PROPS: dict[str, Any] = {
    "success": _BOOL,
    "headline": _STR,
    "_meta": _META,
    "error_code": _STR,
    "message": _STR,
    "retryable": _BOOL,
    "recovery_action": _STR,
    "field_errors": _OBJ_ARRAY,
}


def tool_output_schema(**top_level: dict[str, Any]) -> dict[str, Any]:
    """Build a permissive-but-typed object schema: envelope + tool-specific fields."""
    return {
        "type": "object",
        "properties": {**_BASE_PROPS, **top_level},
        "required": ["success"],
        "additionalProperties": True,
    }


SEARCH_PANELS_SCHEMA = tool_output_schema(
    query=_STR, count=_INT, total=_INT, panels=_OBJ_ARRAY, truncated=_TRUNCATION
)
GET_PANEL_SCHEMA = tool_output_schema(panel=_OBJ)
GET_PANEL_GENES_SCHEMA = tool_output_schema(
    panel=_OBJ_OR_NULL,
    count=_INT,
    total=_INT,
    entities=_OBJ_ARRAY,
    truncated=_TRUNCATION,
)
GET_GENE_PANELS_SCHEMA = tool_output_schema(
    gene=_OBJ, count=_INT, total=_INT, panels=_OBJ_ARRAY, truncated=_TRUNCATION
)
RESOLVE_GENE_SCHEMA = tool_output_schema(query=_STR, gene=_OBJ_OR_NULL, matches=_OBJ_ARRAY)
CAPABILITIES_SCHEMA = tool_output_schema(
    server=_STR,
    server_version=_STR,
    tools=_ARRAY,
    vocabulary=_OBJ,
    response_modes=_OBJ,
    capabilities_version=_STR,
    data=_OBJ,
)
_DIAGNOSTICS_DATA: dict[str, Any] = {
    "type": "object",
    "description": "Live backend status, cache stats, and the RED metrics snapshot.",
    "properties": {
        "mode": _STR,
        "sources": _OBJ,
        "cache_ttl_seconds": _INT,
        "cache": _OBJ,
        "metrics": {
            "type": "object",
            "description": "Process-wide RED aggregates (also at GET /metrics).",
            "properties": {
                "requests_total": _INT,
                "requests_by_tool": _OBJ,
                "errors_total": _INT,
                "errors_by_code": _OBJ,
                "cache": _OBJ,
                "tool_duration_ms": _OBJ,
                "upstream_duration_ms": _OBJ,
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}
DIAGNOSTICS_SCHEMA = tool_output_schema(
    server_version=_STR, capabilities_version=_STR, data=_DIAGNOSTICS_DATA, refresh=_OBJ
)
