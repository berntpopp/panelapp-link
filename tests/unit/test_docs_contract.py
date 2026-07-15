"""Every advertised contract must match what the server actually does.

Same defect class as the `region` enum bug this branch fixes, on the prose
surfaces: a doc, a tool description, a capabilities string, or a resource note
that promises an argument, a lookup, a page cursor, a response field, or an error
code the runtime does not honour is a trap -- an agent that believes it fails. The
oracle is always the live server (``create_panelapp_mcp()``), never a hardcoded
copy of the contract.

The surfaces are DISCOVERED, not listed: every markdown file in the repo, every
module-level string constant in ``panelapp_link.mcp.resources`` (the server
instructions and every MCP resource body), the whole capabilities payload, and
every tool description / title / argument description from the live schemas. A
hardcoded file list is exactly how the previous sweep missed
``docs/data-lifecycle.md``.

Two things are deliberately NOT live-contract surfaces:
  * ``docs/superpowers/`` -- dated design specs and plans. They record what was
    *planned* (an ``AmbiguousQueryError``, an hgnc-id lookup) and were superseded
    by the implementation; rewriting them would falsify the record.
    ``docs/superpowers/README.md`` marks them historical instead.
  * ``CHANGELOG.md`` -- also a historical record, which describes removed
    contracts precisely because they were removed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from panelapp_link.mcp import resources as resources_module
from panelapp_link.mcp.capabilities import build_capabilities
from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.resources import PANELAPP_REFERENCE_NOTES, PANELAPP_USAGE_NOTES
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing

ROOT = Path(__file__).resolve().parents[2]
USAGE_DOC = ROOT / "docs" / "usage.md"

#: Historical records, not live contracts (see the module docstring).
_NOT_A_CONTRACT = ("docs/superpowers/", "CHANGELOG.md")
#: Generated / vendored trees -- not authored prose.
_NOT_AUTHORED = (".venv/", ".pytest_cache/", "node_modules/", "htmlcov/", "site-packages/")


def _markdown_surfaces() -> dict[str, str]:
    """Every markdown file in the repo that states a live contract."""
    out: dict[str, str] = {}
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if any(skip in rel for skip in _NOT_A_CONTRACT + _NOT_AUTHORED):
            continue
        out[rel] = path.read_text(encoding="utf-8")
    return out


def _string_leaves(value: Any) -> list[str]:
    """Every string leaf of a nested capabilities structure."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _string_leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in _string_leaves(v)]
    return []


def _mcp_string_surfaces() -> dict[str, str]:
    """Every server-instruction / MCP-resource string, plus the capabilities payload."""
    out: dict[str, str] = {
        f"resources.{name}": value
        for name, value in vars(resources_module).items()
        if name.isupper() and isinstance(value, str)
    }
    # Leaf-by-leaf (blank-line joined) so two unrelated capabilities values are never
    # glued into one apparent sentence.
    out["capabilities"] = "\n\n".join(_string_leaves(build_capabilities()))
    return out


async def _tool_surfaces() -> dict[str, str]:
    """Every tool's description, title, and argument descriptions."""
    out: dict[str, str] = {}
    for tool in await create_panelapp_mcp().list_tools():
        parts = [tool.description or "", tool.title or ""]
        parts += [
            str(prop.get("description", ""))
            for prop in (tool.parameters or {}).get("properties", {}).values()
        ]
        out[f"tool.{tool.name}"] = " ".join(parts)
    return out


async def _all_surfaces() -> dict[str, str]:
    return {**_markdown_surfaces(), **_mcp_string_surfaces(), **await _tool_surfaces()}


async def _tool_schemas() -> dict[str, dict[str, Any]]:
    return {t.name: (t.parameters or {}) for t in await create_panelapp_mcp().list_tools()}


async def _output_schemas() -> dict[str, dict[str, Any]]:
    return {t.name: (t.output_schema or {}) for t in await create_panelapp_mcp().list_tools()}


def _sentences(prose: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n\n+|\n[-*|] ", prose) if s.strip()]


async def _call(tool: str, args: dict[str, Any], service: Any) -> dict[str, Any]:
    set_service_for_testing(service)
    try:
        async with Client(create_panelapp_mcp()) as client:
            res = await client.call_tool(tool, args, raise_on_error=False)
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()
    return dict(res.structured_content or {})


# --- 0. a guard that scans nothing passes everything ----------------------------


async def test_the_guard_actually_scans_the_docs_and_the_mcp_strings() -> None:
    """Pin the discovered surface set: an empty glob would make every guard vacuous."""
    surfaces = await _all_surfaces()
    for expected in ("README.md", "docs/usage.md", "docs/data-lifecycle.md"):
        assert expected in surfaces, f"{expected} is not being scanned"
    assert "resources.PANELAPP_SERVER_INSTRUCTIONS" in surfaces
    assert "capabilities" in surfaces
    assert "tool.resolve_gene" in surfaces
    assert len(surfaces) > 15


# --- 1. no example may name an argument the tool does not declare ----------------


def _documented_args(prose: str, tool: str) -> set[str]:
    """Argument names used in every ``tool(...)`` example in a prose surface.

    Nested structures (``panels=[{panel_id, region}]``) and quoted values
    (``min_confidence='green'``) are stripped first, so only TOP-LEVEL argument
    names are compared against the tool's declared properties.
    """
    args: set[str] = set()
    for call in re.findall(rf"\b{re.escape(tool)}\(([^)]*)\)", prose):
        flat = re.sub(r"\[[^\]]*\]|\{[^}]*\}|'[^']*'|\"[^\"]*\"", "", call)
        args |= set(re.findall(r"[a-z_][a-z0-9_]*", flat))
    return args


async def test_documented_tool_calls_use_only_declared_arguments() -> None:
    """A `tool(arg=...)` example must not name an argument the schema rejects.

    `resolve_gene(query | gene_symbol | hgnc_id)` was the trap: `resolve_gene` has
    no `hgnc_id` argument at all, so an agent following the docs got an unknown-arg
    rejection.
    """
    schemas = await _tool_schemas()
    for label, prose in (await _all_surfaces()).items():
        for tool, schema in schemas.items():
            declared = set(schema.get("properties", {}))
            used = _documented_args(prose, tool)
            assert used <= declared, (
                f"{label} documents {tool}({', '.join(sorted(used - declared))}) but the "
                f"live schema declares only {sorted(declared)}"
            )


# --- 2. no surface may claim a capability the server does not have ---------------

#: Words that can only appear in a claim this server cannot honour. State the truth
#: without them ("word-prefix", "exactly one gene", ...).
_FALSE_CAPABILITY_WORDS = {
    "substring": (
        "search is word-prefix matching over whole words "
        "(_live_helpers.panel_match_score), so 'renal' does not match 'adrenal'"
    ),
    "ambiguous": (
        "resolve_gene has no ambiguity branch: matches[] is always exactly the one "
        "rolled-up gene, with no ambiguity flag and no ambiguous_query error code"
    ),
    "ambiguity": "the server has no ambiguity concept at all",
    "full-text": (
        "there is no full-text index; search_panels filters the cached panel list in "
        "memory by word-prefix token match"
    ),
    "fts": "no FTS engine exists anywhere in this server",
}


async def test_no_surface_claims_a_capability_the_server_lacks() -> None:
    for label, prose in (await _all_surfaces()).items():
        low = prose.lower()
        for word, why in _FALSE_CAPABILITY_WORDS.items():
            # The WORD, not an identifier that merely contains it (a CodeQL rule id
            # like py/incomplete-url-substring-sanitization is not a claim).
            assert not re.search(rf"(?<![\w/-]){re.escape(word)}(?![\w/-])", low), (
                f"{label} says {word!r}, which is false: {why}"
            )


# --- 3. an HGNC id is not a query the server can answer --------------------------

#: Any mention of HGNC -- "HGNC id", "HGNC CURIE", "HGNC:1100" (the previous version of
#: this guard matched only the CURIE form, and so sailed straight past the server
#: instructions' "resolve_gene to normalize a symbol/HGNC id").
_HGNC = r"\bhgnc\b"
_RESOLUTION_VERB = r"(resolv|normali[sz]|look ?up|lookup|maps? |mapping)"
_NEGATION = r"\b(not|never|cannot|can't|no)\b"


async def test_no_surface_claims_an_hgnc_id_can_be_resolved() -> None:
    """PanelApp is queried by ``entity_name`` (a gene SYMBOL): there is no HGNC lookup.

    ``resolve_gene`` takes ``gene_symbol or query`` and passes it straight to
    ``/genes/?entity_name=`` (``panelapp_service.resolve_gene`` ->
    ``client.get_genes_by_entity_name``), so an HGNC CURIE is used as a literal entity
    name and simply misses. A surface may WARN that HGNC cannot be resolved (negated),
    but it may never offer the lookup -- nor the "just pass it as free text" workaround,
    which is the same false promise wearing a hat.
    """
    for label, prose in (await _all_surfaces()).items():
        for sentence in _sentences(prose):
            if not re.search(_HGNC, sentence, re.IGNORECASE):
                continue
            if re.search(_RESOLUTION_VERB, sentence, re.IGNORECASE):
                assert re.search(_NEGATION, sentence, re.IGNORECASE), (
                    f"{label} implies an HGNC id can be resolved or looked up, which the "
                    f"runtime cannot do: {sentence.strip()!r}"
                )
            assert not re.search(r"free[- ]text", sentence, re.IGNORECASE), (
                f"{label} suggests passing an HGNC id as free text, which just makes it a "
                f"literal entity_name that misses: {sentence.strip()!r}"
            )


async def test_resolve_gene_really_cannot_resolve_an_hgnc_id(live_service: Any) -> None:
    """The behaviour behind the claim: an HGNC CURIE is not a lookup key (issue #25 D3).

    It is rejected up front with ``invalid_input`` (field ``gene_symbol``) rather
    than passed to ``/genes/?entity_name=HGNC:1100``, where it would miss on UK but
    loosely match on AU -- a half-answer with a silently dropped region.
    """
    body = await _call("resolve_gene", {"query": "HGNC:1100"}, live_service)
    assert body["error_code"] == "invalid_input"
    # The field names resolve_gene's ACTUAL parameter (query), never a param the tool
    # does not expose.
    assert body["field_errors"][0]["field"] == "query"


# --- 4. resolve_gene returns exactly one gene; it is not a disambiguator ---------


async def test_resolve_gene_returns_exactly_one_match(live_service: Any) -> None:
    """`matches[]` is always the single rolled-up gene -- never a candidate list."""
    body = await _call("resolve_gene", {"query": "AAAS"}, live_service)
    assert body["success"] is True
    assert len(body["matches"]) == 1
    assert body["matches"][0] == body["gene"]


# --- 5. hgnc_id is a filter, never a standalone query ----------------------------


async def test_hgnc_id_cannot_stand_alone_as_a_query() -> None:
    """The schema must not advertise a query the runtime refuses.

    `resolve_gene` takes no `hgnc_id`, and `get_gene_panels` REQUIRES `gene_symbol`
    (`hgnc_id` is an optional result filter). Otherwise `get_gene_panels(hgnc_id=...)`
    is schema-accepted and then rejected at runtime -- the original bug, again.
    """
    schemas = await _tool_schemas()
    assert "hgnc_id" not in schemas["resolve_gene"].get("properties", {})

    gene_panels = schemas["get_gene_panels"]
    assert "hgnc_id" in gene_panels["properties"]  # still an optional filter
    assert "gene_symbol" in gene_panels.get("required", [])

    async with Client(create_panelapp_mcp()) as client:
        res = await client.call_tool(
            "get_gene_panels", {"hgnc_id": "HGNC:1100"}, raise_on_error=False
        )
    body = res.structured_content
    assert body["error_code"] == "invalid_input"
    assert body["field_errors"][0]["field"] == "gene_symbol"


# --- 6. the error taxonomy is exactly what the server can emit -------------------


def _codes_in(prose: str) -> set[str]:
    known = set(build_capabilities()["error_codes_list"]) | {"ambiguous_query", "data_unavailable"}
    return {code for code in known if re.search(rf"\b{code}\b", prose)}


async def test_documented_error_codes_match_the_advertised_taxonomy() -> None:
    """`ambiguous_query` was documented but no `_classify` branch emits it."""
    advertised = set(build_capabilities()["error_codes_list"])

    taxonomy = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    taxonomy = taxonomy.split("## Error taxonomy")[1].split("##")[0]
    assert _codes_in(taxonomy) == advertised

    codes_sentence = PANELAPP_REFERENCE_NOTES.split("Error codes:")[1].split(".")[0]
    assert _codes_in(codes_sentence) == advertised


# --- 7. only the cursor-taking tools are documented as paged ---------------------


async def test_cursor_paged_tools_are_exactly_the_ones_documented_as_paged() -> None:
    """`get_gene_panels` was advertised as cursor-paged; it has no `cursor` at all."""
    schemas = await _tool_schemas()
    paged = {name for name, s in schemas.items() if "cursor" in s.get("properties", {})}
    assert paged  # sanity: the class is non-empty

    claims = {
        "panelapp://usage": PANELAPP_USAGE_NOTES.split("Paged tools (")[1].split(")")[0],
        "panelapp://reference": PANELAPP_REFERENCE_NOTES.split("Paging contract:")[1].split(".")[0],
    }
    for label, sentence in claims.items():
        named = {name for name in schemas if re.search(rf"\b{re.escape(name)}\b", sentence)}
        assert named == paged, (
            f"{label} lists the paged tools as {sorted(named)}; the live schemas say "
            f"the cursor-paged tools are {sorted(paged)}"
        )


# --- 8. headline is only claimed where it is emitted -----------------------------


async def test_headline_is_only_claimed_where_the_output_schema_declares_it() -> None:
    """Only the diagnostics response carries a `headline`; the docs claimed all did."""
    with_headline = {
        name
        for name, schema in (await _output_schemas()).items()
        if "headline" in schema.get("properties", {})
    }
    assert with_headline == {"get_panelapp_diagnostics"}

    hits = [ln for ln in USAGE_DOC.read_text(encoding="utf-8").splitlines() if "headline" in ln]
    assert hits, "docs/usage.md no longer mentions headline; drop this guard"
    for line in hits:
        assert "get_panelapp_diagnostics" in line, (
            "docs/usage.md must attribute `headline` to get_panelapp_diagnostics -- "
            "no other tool emits it"
        )


# --- 9. the concrete-region contract, checked on the live schema -----------------


@pytest.mark.parametrize("tool_name", ["get_panel", "get_panel_genes"])
async def test_concrete_region_tools_are_not_offered_both(tool_name: str) -> None:
    schema = (await _tool_schemas())[tool_name]
    assert set(schema["properties"]["region"]["enum"]) == {"uk", "australia"}
