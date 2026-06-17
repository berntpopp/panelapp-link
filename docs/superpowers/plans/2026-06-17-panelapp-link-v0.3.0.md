# PanelApp-Link v0.3.0 ("Beyond 9/10" Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the full-mode field-rename contract bug, add two aggregation/batch MCP tools, trim per-call tokens, and light up opt-in OTel — taking the server from 8.5 to >9/10 without breaking the live-API, single-worker posture.

**Architecture:** All new orchestration lands in new modules (`services/aggregations.py`, `mcp/tools/aggregations.py`) so the line-tight `panelapp_service.py` (580/600) is frozen. New tools call only **public** service methods and route through the existing `run_mcp_tool` envelope, inheriting `_meta`/`next_commands`/observability for free. Token and observability changes are surgical edits to `envelope.py`, `shaping.py`, `tracing.py`, `config.py`, and `pyproject.toml`.

**Tech Stack:** Python 3.12, FastMCP 3.x, Pydantic 2, httpx, respx (tests), pytest-asyncio, OpenTelemetry (API always; SDK+OTLP opt-in), `uv`.

## Global Constraints

- **File-size budget: 600 lines per module** in `panelapp_link/`, `server.py`, `mcp_server.py` (enforced by `make lint-loc`). `panelapp_service.py` is at 580 — **must not grow**.
- **Public MCP tools stay read-only, research-use scoped.** Annotations: `READ_ONLY_OPEN_WORLD`.
- **Upstream politeness:** reuse the existing concurrency cap (`max_concurrency=4`), single-flight cache, jittered backoff. No unbounded fan-out (PanelApp 429s per-IP bursts). The batch tool is capped at 20 symbols.
- **Tests never hit the network** except under the `integration` marker; use `respx` + committed fixtures via `tests/conftest.py` (`build_router`, `live_service`).
- **`make ci-local` must pass:** ruff format, ruff lint, `lint-loc`, mypy (strict, py312), pytest, coverage ≥ 85%.
- **Typing:** modern (`list[str]`, `X | None`); Google docstrings; ruff line-length 100.
- **Every commit message ends with the repo trailer:**
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **No schema-breaking change beyond M1.** `entity_counts` keeps integer values; per-confidence data ships as the new additive `confidence_counts`.
- **Branch:** work continues on `beyond-9-v0.3.0` (already checked out).

### Available test fixtures (from `tests/conftest.py`)
- `live_service` fixture: a real `PanelAppService` over respx-mocked httpx.
- UK panel details: `1207`, `285`. AU panel detail: `3149`.
- `/genes/` pages: UK `AAAS`, `HMBS`; AU `PKD1`. Any other id → 404; any other gene → empty page.
- `set_service_for_testing(svc)` / `reset_panelapp_service()` in `panelapp_link.mcp.service_adapters` swap the singleton the MCP tools use.

### Key interfaces this plan introduces
- `services/aggregations.py`:
  - `async def compare_panels(svc, panel_refs: list[dict], *, min_confidence: str | None = None, response_mode: str = "compact") -> dict[str, Any]`
  - `async def panels_for_genes(svc, gene_symbols: list[str], *, region: str = "both", min_confidence: str | None = None, response_mode: str = "compact", cap: int = 20) -> dict[str, Any]`
- `mcp/tools/aggregations.py`: `def register_aggregation_tools(mcp) -> None` registering `compare_panels` and `get_panels_for_genes`.
- `mcp/next_commands.py`: `after_compare_panels(panel_refs)`, `after_panels_for_genes(genes)`.
- `mcp/schemas.py`: `COMPARE_PANELS_SCHEMA`, `GET_PANELS_FOR_GENES_SCHEMA`.

---

## Task 1: WS-1a — Normalize full-mode panel count fields (M1 fix)

**Files:**
- Modify: `panelapp_link/services/shaping.py:176-177` (`shape_panel` full branch)
- Test: `tests/test_shaping_modes.py` (new)

**Interfaces:**
- Consumes: `shape_panel(row, mode)` (existing).
- Produces: `shape_panel` output where `n_genes`/`n_regions`/`n_strs` are present and `number_of_*` absent in **all four** modes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shaping_modes.py`:

```python
"""Mode-invariance of shared panel fields (M1 regression guard)."""

from __future__ import annotations

import pytest

from panelapp_link.models.enums import RESPONSE_MODES
from panelapp_link.services import shaping

_ROW = {
    "panel_id": 283,
    "name": "Cystic kidney disease",
    "region": "uk",
    "number_of_genes": 80,
    "number_of_regions": 2,
    "number_of_strs": 0,
    "version": "9.1",
    "disease_group": "Renal",
    "disease_sub_group": "",
    "status": "public",
    "signed_off_version": "9.0",
    "signed_off_date": "2026-05-06",
    "relevant_disorders": ["Cystic kidney disease"],
    "version_created": "2026-05-06T16:02:21Z",
    "description": None,
    "types": [],
    "entity_counts": {"gene": 80, "region": 2, "str": 0},
}


@pytest.mark.parametrize("mode", RESPONSE_MODES)
def test_panel_count_fields_are_mode_invariant(mode: str) -> None:
    out = shaping.shape_panel(_ROW, mode)
    assert out["n_genes"] == 80
    assert out["n_regions"] == 2
    assert out["n_strs"] == 0
    assert "number_of_genes" not in out
    assert "number_of_regions" not in out
    assert "number_of_strs" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shaping_modes.py -v`
Expected: FAIL on `mode == "full"` — `KeyError: 'n_genes'` / `number_of_genes` present.

- [ ] **Step 3: Implement the fix**

In `panelapp_link/services/shaping.py`, replace the `full` branch of `shape_panel`:

```python
    if mode == "full":
        out = dict(row)
        out["n_genes"] = out.pop("number_of_genes", 0)
        out["n_regions"] = out.pop("number_of_regions", 0)
        out["n_strs"] = out.pop("number_of_strs", 0)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shaping_modes.py -v`
Expected: PASS (all four modes).

- [ ] **Step 5: Commit**

```bash
git add panelapp_link/services/shaping.py tests/test_shaping_modes.py
git commit -m "$(printf 'fix(shaping): normalize full-mode panel counts to n_* (M1)\n\nFull mode returned upstream number_of_* names while other modes used\nn_*; consumers broke when widening verbosity. Remap in the full branch\nso shared count fields are mode-invariant.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: WS-1b — Align "strongest confidence" wording to `max_confidence_label` (L2)

**Files:**
- Modify: `panelapp_link/mcp/tools/genes.py:87-93` (`resolve_gene` description)
- Modify: `panelapp_link/mcp/capabilities.py:148-152` (observability/tracing prose — leave; this is fine) and any `strongest`/`strongest confidence` mention
- Test: `tests/test_capabilities_wording.py` (new)

**Interfaces:**
- Consumes: `build_capabilities()` (existing); `resolve_gene` tool description string.
- Produces: no payload field named `strongest_confidence`; prose references `max_confidence_label`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_capabilities_wording.py`:

```python
"""L2: capabilities/tool prose must name the real field, not 'strongest confidence'."""

from __future__ import annotations

import json

from panelapp_link.mcp.capabilities import build_capabilities


def test_capabilities_has_no_strongest_confidence_phrase() -> None:
    blob = json.dumps(build_capabilities()).lower()
    assert "strongest confidence" not in blob
    assert "strongest_confidence" not in blob
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capabilities_wording.py -v`
Expected: PASS or FAIL depending on current prose. If it already passes (capabilities never used the phrase), still update the `resolve_gene` description in Step 3 and add the description assertion below.

Add to the same test file:

```python
from panelapp_link.mcp.facade import create_panelapp_mcp


async def test_resolve_gene_description_names_field() -> None:
    mcp = create_panelapp_mcp()
    tools = {t.name: t for t in await mcp._list_tools()}
    desc = tools["resolve_gene"].description.lower()
    assert "strongest confidence" not in desc
    assert "max_confidence_label" in desc
```

Run: `uv run pytest tests/test_capabilities_wording.py -v`
Expected: FAIL on `test_resolve_gene_description_names_field` (`max_confidence_label` not in description).

> Note: if `mcp._list_tools()` is not available in this FastMCP version, fetch via an in-memory client instead:
> ```python
> from fastmcp import Client
> async with Client(create_panelapp_mcp()) as c:
>     tools = {t.name: t for t in await c.list_tools()}
> ```

- [ ] **Step 3: Implement the wording change**

In `panelapp_link/mcp/tools/genes.py`, change the `resolve_gene` description's parenthetical:

```python
        description=(
            "Resolve free text or an approved symbol to a single rolled-up PanelApp "
            "gene (symbol, hgnc id, panel count, regions, and max_confidence_label -- "
            "the strongest traffic-light label across panels). Pass query or "
            "gene_symbol. region (uk|australia|both, default both) scopes the lookup. "
            "Follow up with get_gene_panels to list the panels the gene appears on."
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_capabilities_wording.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add panelapp_link/mcp/tools/genes.py tests/test_capabilities_wording.py
git commit -m "$(printf 'docs(mcp): name max_confidence_label in resolve_gene prose (L2)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: WS-1c — Cross-tool count-field conformance guard

**Files:**
- Test: `tests/test_tool_contract_conformance.py` (new)

**Interfaces:**
- Consumes: in-memory FastMCP client, `set_service_for_testing`, `live_service`.
- Produces: durable guard that `search_panels`/`get_panel` never leak `number_of_*` and always expose `n_*` across all modes, at the **tool envelope** layer (not just the pure shaper).

- [ ] **Step 1: Write the failing test (guard; should pass once Task 1 is in)**

Create `tests/test_tool_contract_conformance.py`:

```python
"""Tool-layer contract guard: shared count fields are mode-invariant end-to-end."""

from __future__ import annotations

import pytest
from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing
from panelapp_link.models.enums import RESPONSE_MODES


@pytest.fixture
def mcp_client(live_service):
    set_service_for_testing(live_service)
    try:
        yield Client(create_panelapp_mcp())
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_get_panel_count_fields_mode_invariant(mcp_client, mode: str) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panel", {"panel_id": 1207, "region": "uk", "response_mode": mode},
            raise_on_error=False,
        )
    panel = res.structured_content["panel"]
    assert {"n_genes", "n_regions", "n_strs"} <= panel.keys()
    assert not any(k.startswith("number_of_") for k in panel)


@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_search_panels_count_fields_mode_invariant(mcp_client, mode: str) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "search_panels", {"query": "", "region": "uk", "limit": 3, "response_mode": mode},
            raise_on_error=False,
        )
    for panel in res.structured_content["panels"]:
        assert {"n_genes", "n_regions", "n_strs"} <= panel.keys()
        assert not any(k.startswith("number_of_") for k in panel)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_tool_contract_conformance.py -v`
Expected: PASS (Task 1 already fixed the shaper). If it FAILS, Task 1 is incomplete — fix there.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tool_contract_conformance.py
git commit -m "$(printf 'test(mcp): tool-layer guard for mode-invariant panel count fields\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: WS-4a — Lean `_meta` in minimal mode

**Files:**
- Modify: `panelapp_link/mcp/envelope.py:246-250` (`run_mcp_tool` minimal-mode block)
- Test: `tests/test_envelope_lean_meta.py` (new)

**Interfaces:**
- Consumes: `run_mcp_tool` (existing).
- Produces: minimal-mode `_meta` without `upstream`, `upstream_ms`, `citation_short`; retains `request_id`, `elapsed_ms`, `cache`, `response_mode`, `unsafe_for_clinical_use`, and `next_commands[:1]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_envelope_lean_meta.py`:

```python
"""WS-4: minimal mode drops heavy _meta keys; compact keeps them."""

from __future__ import annotations

import pytest

from panelapp_link.mcp.envelope import run_mcp_tool


async def _body() -> dict:
    return {
        "ok": True,
        "_meta": {
            "next_commands": [{"tool": "a", "arguments": {}}, {"tool": "b", "arguments": {}}],
            "cache": "hit",
            "upstream_ms": 12.0,
            "upstream": {"uk": {"calls": 1, "ms": 12.0}},
        },
    }


async def test_minimal_meta_is_lean() -> None:
    out = await run_mcp_tool("search_panels", _body, response_mode="minimal")
    meta = out["_meta"]
    assert "upstream" not in meta
    assert "upstream_ms" not in meta
    assert "citation_short" not in meta
    assert len(meta["next_commands"]) == 1
    assert meta["request_id"]
    assert "elapsed_ms" in meta


async def test_compact_meta_keeps_breadcrumbs() -> None:
    out = await run_mcp_tool("search_panels", _body, response_mode="compact")
    meta = out["_meta"]
    assert meta["citation_short"]
    assert "upstream_ms" in meta
```

> Note: `_body`'s injected `_meta.upstream*` may be overwritten by real telemetry (which is empty here). The assertions target presence/absence after the minimal trim, which is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_envelope_lean_meta.py -v`
Expected: FAIL on `test_minimal_meta_is_lean` — `citation_short`/`upstream*` still present.

- [ ] **Step 3: Implement the lean trim**

In `panelapp_link/mcp/envelope.py`, replace the minimal-mode block inside `run_mcp_tool`:

```python
                # Minimal mode is for sweep/agent-loop workloads: shed per-call
                # token tax -- one next step, and drop upstream timing + the
                # redundant short citation (the citation_ref stub still rides).
                if response_mode == "minimal":
                    for heavy in ("upstream", "upstream_ms", "citation_short"):
                        meta.pop(heavy, None)
                    if meta.get("next_commands"):
                        meta["next_commands"] = meta["next_commands"][:1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_envelope_lean_meta.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing observability tests (no regression)**

Run: `uv run pytest tests/test_envelope_observability.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/envelope.py tests/test_envelope_lean_meta.py
git commit -m "$(printf 'perf(mcp): lean _meta in minimal mode (drop upstream + short citation)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: WS-6a — Additive `confidence_counts` on panel detail

**Files:**
- Modify: `panelapp_link/services/shaping.py` (`normalize_panel` + `shape_panel` standard branch + new `_confidence_counts` helper)
- Test: `tests/test_confidence_counts.py` (new)

**Interfaces:**
- Consumes: `normalize_panel(live, region, signed_off)` (existing).
- Produces: when the live payload is a detail (has `genes`/`regions`/`strs`), `normalize_panel` adds `confidence_counts = {"gene": {"green":N,"amber":N,"red":N}, ...}`; `shape_panel` exposes it in `standard` and `full` modes. `entity_counts` is unchanged (integers).

- [ ] **Step 1: Write the failing test**

Create `tests/test_confidence_counts.py`:

```python
"""WS-6a: additive confidence_counts; entity_counts stays integer."""

from __future__ import annotations

from panelapp_link.services import shaping

_DETAIL = {
    "id": 283,
    "name": "Cystic kidney disease",
    "stats": {"number_of_genes": 3, "number_of_regions": 0, "number_of_strs": 0},
    "genes": [
        {"entity_type": "gene", "entity_name": "PKD1", "confidence_level": "3",
         "gene_data": {"gene_symbol": "PKD1"}},
        {"entity_type": "gene", "entity_name": "PKD2", "confidence_level": "3",
         "gene_data": {"gene_symbol": "PKD2"}},
        {"entity_type": "gene", "entity_name": "GANAB", "confidence_level": "2",
         "gene_data": {"gene_symbol": "GANAB"}},
    ],
}


def test_normalize_adds_confidence_counts() -> None:
    row = shaping.normalize_panel(_DETAIL, "uk")
    assert row["entity_counts"] == {"gene": 3, "region": 0, "str": 0}  # unchanged, integers
    assert row["confidence_counts"]["gene"] == {"green": 2, "amber": 1, "red": 0}


def test_standard_exposes_confidence_counts_compact_does_not() -> None:
    row = shaping.normalize_panel(_DETAIL, "uk")
    assert "confidence_counts" in shaping.shape_panel(row, "standard")
    assert "confidence_counts" in shaping.shape_panel(row, "full")
    assert "confidence_counts" not in shaping.shape_panel(row, "compact")
    assert "confidence_counts" not in shaping.shape_panel(row, "minimal")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_confidence_counts.py -v`
Expected: FAIL — `confidence_counts` not built.

- [ ] **Step 3: Implement the helper + wiring**

In `panelapp_link/services/shaping.py`, add the helper (near `normalize_panel`):

```python
def _confidence_counts(live: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per-entity-type traffic-light tallies from a panel detail payload."""
    out: dict[str, dict[str, int]] = {}
    for etype, key in (("gene", "genes"), ("region", "regions"), ("str", "strs")):
        items = live.get(key)
        if not items:
            continue
        counts = {"green": 0, "amber": 0, "red": 0}
        for item in items:
            level = _as_str_or_none(item.get("confidence_level"))
            label = confidence_label(level) if level is not None else None
            if label in counts:
                counts[label] += 1
        out[etype] = counts
    return out
```

In `normalize_panel`, extend the detail block:

```python
    if any(key in live for key in ("genes", "regions", "strs")):
        out["entity_counts"] = {
            "gene": len(live.get("genes") or []),
            "region": len(live.get("regions") or []),
            "str": len(live.get("strs") or []),
        }
        out["confidence_counts"] = _confidence_counts(live)
    return out
```

In `shape_panel`, add to the `# standard` block (alongside `entity_counts`):

```python
    # standard
    out.update(
        {
            "version_created": row.get("version_created"),
            "description": row.get("description"),
            "types": row.get("types", []),
            "entity_counts": row.get("entity_counts", {}),
            "confidence_counts": row.get("confidence_counts", {}),
        }
    )
    return out
```

Update the module docstring "Verbosity contract (panels)" `standard` line to mention `confidence_counts`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_confidence_counts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add panelapp_link/services/shaping.py tests/test_confidence_counts.py
git commit -m "$(printf 'feat(shaping): additive confidence_counts on panel detail (standard+)\n\nentity_counts unchanged (integers); confidence_counts is a new sibling.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: WS-2 — `compare_panels` aggregation (service logic)

**Files:**
- Create: `panelapp_link/services/aggregations.py`
- Test: `tests/test_aggregations_compare.py` (new)

**Interfaces:**
- Consumes: `svc.get_panel(panel_id, region, response_mode)` → `{"panel": {...}}`; `svc.get_panel_genes(panel_id, region, entity_type, min_confidence, response_mode, cursor)` → `{"entities":[...], "truncated"?}`; `InvalidInputError`.
- Produces: `async def compare_panels(svc, panel_refs, *, min_confidence=None, response_mode="compact") -> dict` returning `{"panels", "shared", "only_in", "confidence_deltas", "summary"}` (deltas/maps gated by mode per the spec contract).

- [ ] **Step 1: Write the failing test**

Create `tests/test_aggregations_compare.py`:

```python
"""WS-2: compare_panels gene-level diff over a stub service (deterministic)."""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import InvalidInputError
from panelapp_link.services import aggregations


class _StubSvc:
    """Minimal stand-in exposing get_panel + get_panel_genes."""

    def __init__(self, genes_by_ref: dict[tuple[int, str], list[dict]]) -> None:
        self._genes = genes_by_ref

    async def get_panel(self, panel_id: int, region: str, response_mode: str = "compact") -> dict:
        ents = self._genes[(panel_id, region)]
        return {"panel": {"panel_id": panel_id, "region": region, "name": f"P{panel_id}",
                          "n_genes": len(ents)}}

    async def get_panel_genes(self, panel_id, region, entity_type="gene", min_confidence=None,
                              response_mode="compact", cursor=None) -> dict:
        return {"entities": list(self._genes[(panel_id, region)])}


def _g(symbol: str, label: str) -> dict:
    return {"gene_symbol": symbol, "entity_name": symbol, "confidence_label": label}


async def test_self_compare_is_full_overlap() -> None:
    svc = _StubSvc({(1, "uk"): [_g("A", "green"), _g("B", "amber")]})
    out = await aggregations.compare_panels(svc, [{"panel_id": 1, "region": "uk"},
                                                  {"panel_id": 1, "region": "uk"}])
    assert out["shared"] == ["A", "B"]
    assert out["only_in"] == {"1@uk": []}
    assert out["summary"] == {"n_shared": 2, "n_union": 2}


async def test_two_panel_union_math_and_deltas() -> None:
    svc = _StubSvc({
        (1, "uk"): [_g("A", "green"), _g("B", "amber")],
        (2, "uk"): [_g("A", "amber"), _g("C", "green")],
    })
    out = await aggregations.compare_panels(
        svc, [{"panel_id": 1, "region": "uk"}, {"panel_id": 2, "region": "uk"}]
    )
    assert out["shared"] == ["A"]
    assert out["only_in"]["1@uk"] == ["B"]
    assert out["only_in"]["2@uk"] == ["C"]
    assert out["summary"] == {"n_shared": 1, "n_union": 3}
    assert out["confidence_deltas"] == [
        {"gene_symbol": "A", "per_panel": {"1@uk": "green", "2@uk": "amber"}}
    ]


async def test_rejects_fewer_than_two_or_region_both() -> None:
    svc = _StubSvc({(1, "uk"): []})
    with pytest.raises(InvalidInputError):
        await aggregations.compare_panels(svc, [{"panel_id": 1, "region": "uk"}])
    with pytest.raises(InvalidInputError):
        await aggregations.compare_panels(
            svc, [{"panel_id": 1, "region": "both"}, {"panel_id": 2, "region": "uk"}]
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aggregations_compare.py -v`
Expected: FAIL — `panelapp_link.services.aggregations` does not exist.

- [ ] **Step 3: Implement `aggregations.py` (compare half)**

Create `panelapp_link/services/aggregations.py`:

```python
"""Cross-panel / cross-gene aggregation orchestration.

Free functions that compose the *public* PanelAppService methods (so the
line-tight service body stays frozen) into higher-order, token-saving views:
``compare_panels`` (gene-level diff) and ``panels_for_genes`` (batch membership).
All fan-out rides the service's cache + concurrency-capped client.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from panelapp_link.exceptions import InvalidInputError, NotFoundError

_MIN_PANELS = 2
_MAX_PANELS = 5


class _Service(Protocol):
    async def get_panel(self, panel_id: int, region: str, response_mode: str = ...) -> dict[str, Any]: ...
    async def get_panel_genes(  # noqa: PLR0913
        self, panel_id: int, region: str, entity_type: str = ..., min_confidence: str | None = ...,
        response_mode: str = ..., cursor: str | None = ...,
    ) -> dict[str, Any]: ...
    async def get_gene_panels(
        self, gene_symbol: str | None = ..., hgnc_id: str | None = ..., region: str = ...,
        min_confidence: str | None = ..., response_mode: str = ...,
    ) -> dict[str, Any]: ...


def _ref_key(ref: dict[str, Any]) -> str:
    return f"{ref['panel_id']}@{ref['region']}"


def _validate_refs(panel_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not (_MIN_PANELS <= len(panel_refs) <= _MAX_PANELS):
        raise InvalidInputError(
            f"compare_panels needs {_MIN_PANELS}-{_MAX_PANELS} panels.", field="panels"
        )
    out: list[dict[str, Any]] = []
    for ref in panel_refs:
        region = ref.get("region")
        panel_id = ref.get("panel_id")
        if region not in ("uk", "australia"):
            raise InvalidInputError(
                "region must be 'uk' or 'australia' per panel (panel ids are "
                "per-region; 'both' is not allowed).",
                field="region",
            )
        if not isinstance(panel_id, int):
            raise InvalidInputError("panel_id must be an integer.", field="panel_id")
        out.append({"panel_id": panel_id, "region": region})
    return out


async def _all_genes(
    svc: _Service, panel_id: int, region: str, min_confidence: str | None, response_mode: str
) -> list[dict[str, Any]]:
    """Page through every gene entity of a panel (panel detail is cached after page 1)."""
    entities: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        res = await svc.get_panel_genes(
            panel_id, region, entity_type="gene", min_confidence=min_confidence,
            response_mode=response_mode, cursor=cursor,
        )
        entities.extend(res.get("entities", []))
        cursor = (res.get("truncated") or {}).get("next_cursor")
        if not cursor:
            return entities


async def compare_panels(
    svc: _Service,
    panel_refs: list[dict[str, Any]],
    *,
    min_confidence: str | None = None,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Diff genes across 2-5 panels: shared / only-in / confidence deltas."""
    refs = _validate_refs(panel_refs)
    metas, gene_lists = await asyncio.gather(
        asyncio.gather(*(svc.get_panel(r["panel_id"], r["region"], "compact") for r in refs)),
        asyncio.gather(
            *(_all_genes(svc, r["panel_id"], r["region"], min_confidence, response_mode)
              for r in refs)
        ),
    )

    keys = [_ref_key(r) for r in refs]
    by_symbol: dict[str, dict[str, str | None]] = {}
    per_ref_symbols: list[set[str]] = []
    for key, ents in zip(keys, gene_lists, strict=True):
        symbols: set[str] = set()
        for ent in ents:
            sym = ent.get("gene_symbol")
            if not sym:
                continue
            symbols.add(sym)
            by_symbol.setdefault(sym, {})[key] = ent.get("confidence_label")
        per_ref_symbols.append(symbols)

    union = sorted(set().union(*per_ref_symbols)) if per_ref_symbols else []
    shared = sorted(s for s in union if all(s in ps for ps in per_ref_symbols))
    only_in = {
        key: sorted(s for s in ps if not all(s in other for other in per_ref_symbols))
        for key, ps in zip(keys, per_ref_symbols, strict=True)
    }

    panels_out: list[dict[str, Any]] = []
    for key, meta, ps in zip(keys, metas, per_ref_symbols, strict=True):
        panel = meta.get("panel") or {}
        if response_mode == "minimal":
            panels_out.append({"panel_id": panel.get("panel_id"), "region": panel.get("region")})
        else:
            panels_out.append({
                "panel_id": panel.get("panel_id"), "region": panel.get("region"),
                "name": panel.get("name"), "n_genes": len(ps),
            })

    out: dict[str, Any] = {
        "panels": panels_out,
        "shared": shared,
        "only_in": only_in,
        "summary": {"n_shared": len(shared), "n_union": len(union)},
    }
    if response_mode == "minimal":
        return out

    if response_mode in ("standard", "full"):
        out["confidence_deltas"] = [
            {"gene_symbol": s, "per_panel": by_symbol[s]} for s in shared
        ]
    else:  # compact: only genes whose label differs across panels
        out["confidence_deltas"] = [
            {"gene_symbol": s, "per_panel": by_symbol[s]}
            for s in shared
            if len({by_symbol[s].get(k) for k in keys}) > 1
        ]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aggregations_compare.py -v`
Expected: PASS.

- [ ] **Step 5: Run mypy on the new module**

Run: `uv run mypy panelapp_link/services/aggregations.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/services/aggregations.py tests/test_aggregations_compare.py
git commit -m "$(printf 'feat(services): compare_panels gene-level diff aggregation\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: WS-3 — `panels_for_genes` batch (service logic)

**Files:**
- Modify: `panelapp_link/services/aggregations.py` (add `panels_for_genes`)
- Test: `tests/test_aggregations_batch.py` (new)

**Interfaces:**
- Consumes: `svc.get_gene_panels(gene_symbol=..., region=..., min_confidence=..., response_mode=...)` (raises `NotFoundError` for unknown symbol).
- Produces: `async def panels_for_genes(svc, gene_symbols, *, region="both", min_confidence=None, response_mode="compact", cap=20) -> dict` returning `{"genes": {sym: {...}}, "not_found": [...], "truncated"?}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_aggregations_batch.py`:

```python
"""WS-3: panels_for_genes batch membership with per-symbol error isolation."""

from __future__ import annotations

import pytest

from panelapp_link.exceptions import DownloadError, NotFoundError
from panelapp_link.services import aggregations


class _StubSvc:
    def __init__(self, known: dict[str, dict], fail: set[str] | None = None) -> None:
        self._known = known
        self._fail = fail or set()

    async def get_gene_panels(self, gene_symbol=None, hgnc_id=None, region="both",
                              min_confidence=None, response_mode="compact") -> dict:
        sym = (gene_symbol or "").upper()
        if sym in self._fail:
            raise DownloadError("upstream 503", status_code=503)
        if sym not in self._known:
            raise NotFoundError(f"No PanelApp gene found for {sym!r}.")
        return self._known[sym]


def _gene_payload(sym: str, count: int, label: str) -> dict:
    return {
        "gene": {"gene_symbol": sym, "panel_count": count, "max_confidence_label": label},
        "panels": [{"panel_id": 1, "region": "uk", "confidence_label": label}],
    }


async def test_mixed_found_and_not_found() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")})
    out = await aggregations.panels_for_genes(svc, ["PKD1", "NOPE"], region="both")
    assert out["genes"]["PKD1"]["panel_count"] == 19
    assert out["genes"]["PKD1"]["max_confidence_label"] == "green"
    assert out["not_found"] == ["NOPE"]


async def test_operational_error_fails_whole_batch() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")}, fail={"BOOM"})
    with pytest.raises(DownloadError):
        await aggregations.panels_for_genes(svc, ["PKD1", "BOOM"])


async def test_cap_truncates_with_note() -> None:
    svc = _StubSvc({})
    symbols = [f"G{i}" for i in range(25)]
    out = await aggregations.panels_for_genes(svc, symbols, cap=20)
    assert out["truncated"]["requested"] == 25
    assert out["truncated"]["processed"] == 20
    assert len(out["not_found"]) == 20


async def test_minimal_mode_omits_panels() -> None:
    svc = _StubSvc({"PKD1": _gene_payload("PKD1", 19, "green")})
    out = await aggregations.panels_for_genes(svc, ["PKD1"], response_mode="minimal")
    assert "panels" not in out["genes"]["PKD1"]
    assert out["genes"]["PKD1"]["panel_count"] == 19
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aggregations_batch.py -v`
Expected: FAIL — `panels_for_genes` not defined.

- [ ] **Step 3: Implement `panels_for_genes`**

Append to `panelapp_link/services/aggregations.py`:

```python
async def panels_for_genes(
    svc: _Service,
    gene_symbols: list[str],
    *,
    region: str = "both",
    min_confidence: str | None = None,
    response_mode: str = "compact",
    cap: int = 20,
) -> dict[str, Any]:
    """Batch gene->panel membership with per-symbol NotFound isolation.

    Unknown symbols collect into ``not_found``; operational errors (download /
    rate-limit) propagate and fail the whole call (retryable envelope upstream).
    """
    cleaned = [s.strip().upper() for s in gene_symbols if s and s.strip()]
    deduped = list(dict.fromkeys(cleaned))  # order-preserving unique
    if not deduped:
        raise InvalidInputError("Provide at least one gene_symbol.", field="gene_symbols")
    processed = deduped[:cap]

    async def _one(symbol: str) -> tuple[str, dict[str, Any] | None]:
        try:
            res = await svc.get_gene_panels(
                gene_symbol=symbol, region=region, min_confidence=min_confidence,
                response_mode=response_mode,
            )
            return symbol, res
        except NotFoundError:
            return symbol, None

    # DownloadError / RateLimitError propagate out of gather -> envelope fails.
    results = await asyncio.gather(*(_one(s) for s in processed))

    genes: dict[str, Any] = {}
    not_found: list[str] = []
    for symbol, res in results:
        if res is None:
            not_found.append(symbol)
            continue
        gene = res.get("gene") or {}
        entry: dict[str, Any] = {
            "panel_count": gene.get("panel_count", 0),
            "max_confidence_label": gene.get("max_confidence_label"),
        }
        if response_mode != "minimal":
            entry["panels"] = res.get("panels", [])
        genes[symbol] = entry

    out: dict[str, Any] = {"genes": genes, "not_found": not_found}
    if len(deduped) > cap:
        out["truncated"] = {
            "requested": len(deduped),
            "processed": cap,
            "hint": f"cap is {cap} symbols per call; resubmit the remaining {len(deduped) - cap}.",
        }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aggregations_batch.py -v`
Expected: PASS.

- [ ] **Step 5: Verify line budget + mypy**

Run: `uv run python scripts/check_file_size.py panelapp_link/services/aggregations.py || wc -l panelapp_link/services/aggregations.py`
Run: `uv run mypy panelapp_link/services/aggregations.py`
Expected: ≤ 600 lines; no mypy errors.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/services/aggregations.py tests/test_aggregations_batch.py
git commit -m "$(printf 'feat(services): panels_for_genes batch membership (per-symbol isolation)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: WS-2/WS-3 — Output schemas + next_commands builders

**Files:**
- Modify: `panelapp_link/mcp/schemas.py` (add two schemas)
- Modify: `panelapp_link/mcp/next_commands.py` (add two builders)
- Test: `tests/test_next_commands_aggregations.py` (new)

**Interfaces:**
- Produces: `COMPARE_PANELS_SCHEMA`, `GET_PANELS_FOR_GENES_SCHEMA`; `after_compare_panels(panel_refs) -> list`, `after_panels_for_genes(genes) -> list`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_next_commands_aggregations.py`:

```python
from __future__ import annotations

from panelapp_link.mcp import next_commands as nc


def test_after_compare_panels_emits_panel_genes_per_ref() -> None:
    cmds = nc.after_compare_panels([{"panel_id": 1, "region": "uk"},
                                    {"panel_id": 2, "region": "australia"}])
    assert cmds[0] == {"tool": "get_panel_genes", "arguments": {"panel_id": 1, "region": "uk"}}
    assert {"tool": "get_panel_genes",
            "arguments": {"panel_id": 2, "region": "australia"}} in cmds
    assert len(cmds) <= 5


def test_after_panels_for_genes_emits_gene_panels_for_found() -> None:
    cmds = nc.after_panels_for_genes({"PKD1": {"panel_count": 19}, "PKD2": {"panel_count": 3}})
    assert {"tool": "get_gene_panels", "arguments": {"gene_symbol": "PKD1"}} in cmds
    assert len(cmds) <= 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_next_commands_aggregations.py -v`
Expected: FAIL — builders not defined.

- [ ] **Step 3: Add the next_commands builders**

Append to `panelapp_link/mcp/next_commands.py` (before `recovery_commands`):

```python
def after_compare_panels(panel_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After a comparison: drill into each compared panel's genes."""
    nexts = [
        cmd("get_panel_genes", panel_id=r.get("panel_id"), region=r.get("region"))
        for r in panel_refs
        if r.get("panel_id") is not None and r.get("region") in ("uk", "australia")
    ]
    return nexts[:_MAX_NEXT_COMMANDS]


def after_panels_for_genes(genes: dict[str, Any]) -> list[dict[str, Any]]:
    """After a batch lookup: list each found gene's full panel footprint."""
    nexts = [cmd("get_gene_panels", gene_symbol=sym) for sym in genes]
    return nexts[:_MAX_NEXT_COMMANDS]
```

- [ ] **Step 4: Add the output schemas**

In `panelapp_link/mcp/schemas.py`, after `RESOLVE_GENE_SCHEMA`:

```python
COMPARE_PANELS_SCHEMA = tool_output_schema(
    panels=_OBJ_ARRAY,
    shared=_ARRAY,
    only_in=_OBJ,
    confidence_deltas=_OBJ_ARRAY,
    summary=_OBJ,
)
GET_PANELS_FOR_GENES_SCHEMA = tool_output_schema(
    genes=_OBJ,
    not_found=_ARRAY,
    truncated=_TRUNCATION,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_next_commands_aggregations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/schemas.py panelapp_link/mcp/next_commands.py tests/test_next_commands_aggregations.py
git commit -m "$(printf 'feat(mcp): schemas + next_commands for aggregation tools\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 9: WS-2/WS-3 — Register the two MCP tools

**Files:**
- Create: `panelapp_link/mcp/tools/aggregations.py`
- Modify: `panelapp_link/mcp/tools/__init__.py`
- Test: `tests/test_tools_aggregations.py` (new)

**Interfaces:**
- Consumes: `compare_panels`, `panels_for_genes` (Tasks 6-7); schemas + builders (Task 8); `get_panelapp_service`, `run_mcp_tool`, `McpErrorContext`.
- Produces: registered tools `compare_panels` and `get_panels_for_genes` on the FastMCP instance.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_aggregations.py`:

```python
"""WS-2/WS-3 end-to-end through the MCP client over the fixture service."""

from __future__ import annotations

import pytest
from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp
from panelapp_link.mcp.service_adapters import reset_panelapp_service, set_service_for_testing


@pytest.fixture
def mcp_client(live_service):
    set_service_for_testing(live_service)
    try:
        yield Client(create_panelapp_mcp())
    finally:
        set_service_for_testing(None)
        reset_panelapp_service()


async def test_compare_panels_self_overlap(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": 1207, "region": "uk"},
                        {"panel_id": 1207, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert body["only_in"]["1207@uk"] == []
    assert body["summary"]["n_shared"] == body["summary"]["n_union"]


async def test_compare_panels_region_both_rejected(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "compare_panels",
            {"panels": [{"panel_id": 1207, "region": "both"},
                        {"panel_id": 285, "region": "uk"}]},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is False
    assert body["error_code"] == "invalid_input"
    assert body["field_errors"][0]["field"] == "region"


async def test_get_panels_for_genes_mixed(mcp_client) -> None:
    async with mcp_client as client:
        res = await client.call_tool(
            "get_panels_for_genes",
            {"gene_symbols": ["AAAS", "MADEUPGENE"], "region": "uk"},
            raise_on_error=False,
        )
    body = res.structured_content
    assert body["success"] is True
    assert "AAAS" in body["genes"]
    assert body["not_found"] == ["MADEUPGENE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools_aggregations.py -v`
Expected: FAIL — tools not registered (`Unknown tool`).

- [ ] **Step 3: Implement the tool module**

Create `panelapp_link/mcp/tools/aggregations.py`:

```python
"""Aggregation tools: compare_panels and get_panels_for_genes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from panelapp_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from panelapp_link.mcp.envelope import McpErrorContext, run_mcp_tool
from panelapp_link.mcp.next_commands import after_compare_panels, after_panels_for_genes
from panelapp_link.mcp.schemas import COMPARE_PANELS_SCHEMA, GET_PANELS_FOR_GENES_SCHEMA
from panelapp_link.mcp.service_adapters import get_panelapp_service
from panelapp_link.models.enums import ConfidenceLabel, Region, ResponseMode
from panelapp_link.services import aggregations

if TYPE_CHECKING:
    from fastmcp import FastMCP

_MODE = Annotated[
    ResponseMode,
    Field(description="Verbosity: minimal | compact | standard | full (default compact)."),
]
_REGION = Annotated[Region, Field(description="uk | australia | both (default).")]
_MIN_CONFIDENCE = Annotated[
    ConfidenceLabel | None, Field(description="green | amber | red rank floor; default no filter.")
]
_PANELS = Annotated[
    list[dict[str, Any]],
    Field(description="2-5 panel refs: [{panel_id:int, region:'uk'|'australia'}]."),
]
_SYMBOLS = Annotated[
    list[str], Field(description="Approved gene symbols (e.g. PKD1); capped at 20 per call.")
]


def register_aggregation_tools(mcp: FastMCP) -> None:
    """Register the aggregation tools (compare_panels, get_panels_for_genes)."""

    @mcp.tool(
        name="compare_panels",
        title="Compare PanelApp Panels",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=COMPARE_PANELS_SCHEMA,
        tags={"panel", "compare"},
        description=(
            "Diff genes across 2-5 panels server-side: shared genes, genes unique to "
            "each panel, and per-panel confidence deltas. Pass concrete-region refs "
            "({panel_id, region}); 'both' is rejected. Cheaper than pulling each "
            "panel's full gene list and diffing in context."
        ),
    )
    async def compare_panels(
        panels: _PANELS,
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await aggregations.compare_panels(
                get_panelapp_service(), panels,
                min_confidence=min_confidence, response_mode=response_mode,
            )
            payload["_meta"] = {"next_commands": after_compare_panels(payload.get("panels", []))}
            return payload

        return await run_mcp_tool(
            "compare_panels", call,
            context=McpErrorContext("compare_panels", arguments={"panels": panels}),
            response_mode=response_mode,
        )

    @mcp.tool(
        name="get_panels_for_genes",
        title="Get Panels for Many Genes",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=GET_PANELS_FOR_GENES_SCHEMA,
        tags={"gene", "batch"},
        description=(
            "Batch gene->panel membership for up to 20 gene symbols in one call: per "
            "gene, the panel_count, max_confidence_label, and panels it appears on. "
            "Unknown symbols are returned in not_found; over-cap input is truncated."
        ),
    )
    async def get_panels_for_genes(
        gene_symbols: _SYMBOLS,
        region: _REGION = "both",
        min_confidence: _MIN_CONFIDENCE = None,
        response_mode: _MODE = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await aggregations.panels_for_genes(
                get_panelapp_service(), gene_symbols,
                region=region, min_confidence=min_confidence, response_mode=response_mode,
            )
            payload["_meta"] = {"next_commands": after_panels_for_genes(payload.get("genes", {}))}
            return payload

        return await run_mcp_tool(
            "get_panels_for_genes", call,
            context=McpErrorContext(
                "get_panels_for_genes", arguments={"gene_symbols": gene_symbols}
            ),
            response_mode=response_mode,
        )
```

- [ ] **Step 4: Wire into the registry**

In `panelapp_link/mcp/tools/__init__.py`, import and register, and extend `__all__`:

```python
from panelapp_link.mcp.tools.aggregations import register_aggregation_tools
from panelapp_link.mcp.tools.discovery import register_discovery_tools
from panelapp_link.mcp.tools.genes import register_gene_tools
from panelapp_link.mcp.tools.panels import register_panel_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = [
    "register_aggregation_tools",
    "register_all_tools",
    "register_discovery_tools",
    "register_gene_tools",
    "register_panel_tools",
]


def register_all_tools(mcp: FastMCP) -> None:
    """Register every PanelApp-Link tool on ``mcp``."""
    register_panel_tools(mcp)
    register_gene_tools(mcp)
    register_aggregation_tools(mcp)
    register_discovery_tools(mcp)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_tools_aggregations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/tools/aggregations.py panelapp_link/mcp/tools/__init__.py tests/test_tools_aggregations.py
git commit -m "$(printf 'feat(mcp): register compare_panels + get_panels_for_genes tools\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 10: WS-6b — Register tools in capabilities + workflows

**Files:**
- Modify: `panelapp_link/mcp/capabilities.py` (`TOOLS`, `tool_defaults`, `recommended_workflows`, `resources`/usage prose as needed)
- Modify: `panelapp_link/mcp/resources.py` (`PANELAPP_USAGE_NOTES` — append the two tools)
- Test: `tests/test_capabilities_tools.py` (new)

**Interfaces:**
- Produces: capabilities `tools` includes the two new names; workflows mention them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_capabilities_tools.py`:

```python
from __future__ import annotations

from panelapp_link.mcp.capabilities import build_capabilities


def test_capabilities_lists_nine_tools() -> None:
    caps = build_capabilities()
    assert "compare_panels" in caps["tools"]
    assert "get_panels_for_genes" in caps["tools"]
    assert len(caps["tools"]) == 9


def test_workflows_mention_new_tools() -> None:
    workflows = " ".join(build_capabilities()["recommended_workflows"])
    assert "compare_panels" in workflows
    assert "get_panels_for_genes" in workflows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capabilities_tools.py -v`
Expected: FAIL — 7 tools, no mention.

- [ ] **Step 3: Update capabilities**

In `panelapp_link/mcp/capabilities.py`, extend `TOOLS`:

```python
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
```

Add to `tool_defaults` (inside `_static_surface`):

```python
            "compare_panels": "compact",
            "get_panels_for_genes": "compact",
```

Append to `recommended_workflows`:

```python
            "compare two panels' genes -> compare_panels(panels=[{panel_id, region}, ...])",
            "triage a gene list -> get_panels_for_genes(gene_symbols=[...], min_confidence='green')",
```

> `_static_surface` is `@functools.cache`d and the hash is recomputed from the surface, so `capabilities_version` updates automatically. No manual hash edit.
>
> **Deviation from spec WS-6 (deliberate):** the spec floated an auto `compare_panels` breadcrumb appended to `search_panels` results. That is dropped to avoid destabilizing the existing `after_search_panels` next_commands contract (and its tests); `compare_panels` is discoverable via capabilities `recommended_workflows`, `panelapp://usage`, and its own `after_compare_panels` breadcrumb — symmetric with `get_panels_for_genes` being user-driven.

- [ ] **Step 4: Update usage prose**

In `panelapp_link/mcp/resources.py`, append to `PANELAPP_USAGE_NOTES` a sentence:

```
" Compare gene sets across panels with compare_panels(panels=[{panel_id, region}, ...]); look up many genes at once with get_panels_for_genes(gene_symbols=[...]) (max 20)."
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_capabilities_tools.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/capabilities.py panelapp_link/mcp/resources.py tests/test_capabilities_tools.py
git commit -m "$(printf 'feat(mcp): advertise aggregation tools in capabilities + usage\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 11: WS-5 — Opt-in OTel SDK + OTLP exporter (stdio-safe)

**Files:**
- Modify: `pyproject.toml` (add `[project.optional-dependencies] otel`)
- Modify: `panelapp_link/config.py` (nested `otel` config + `gene_batch_cap`)
- Modify: `panelapp_link/observability/tracing.py` (`setup_tracing`)
- Modify: `panelapp_link/server_manager.py` (call `setup_tracing` in `_lifespan` and `start_stdio_server`)
- Test: `tests/test_tracing_setup.py` (new)

**Interfaces:**
- Produces: `setup_tracing() -> bool` (True when a provider was installed); gated by `settings.otel.enabled`; console exporter only when `settings.otel.console` and transport is not stdio; OTLP-only otherwise.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracing_setup.py`:

```python
"""WS-5: OTel bootstrap gating + stdio safety + span capture."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from panelapp_link.config import settings
from panelapp_link.observability import tracing


def test_setup_tracing_disabled_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(settings.otel, "enabled", False)
    assert tracing.setup_tracing() is False


def test_tool_span_records_under_a_provider() -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        with tracing.tool_span("compare_panels", "abc123", {}):
            pass
        names = [s.name for s in exporter.get_finished_spans()]
        assert "mcp.tool/compare_panels" in names
    finally:
        provider.shutdown()
```

> Note: `trace.set_tracer_provider` can only be set once per process; if another test set one, the second test still records because `_TRACER` is a proxy that resolves the global provider lazily. If the suite complains about override, mark this module to run in its own file (already is) — it is isolated.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tracing_setup.py -v`
Expected: FAIL on `test_setup_tracing_disabled_returns_false` — `settings.otel` / `setup_tracing` missing.

- [ ] **Step 3: Add nested OTel config + gene cap**

In `panelapp_link/config.py`, add a model and fields:

```python
class OtelConfigModel(BaseModel):
    """OpenTelemetry tracing toggle (opt-in; default no-op)."""

    enabled: bool = Field(default=False, description="Install an OTLP TracerProvider on startup.")
    console: bool = Field(
        default=False,
        description="Also export spans to stderr (dev only; disabled under stdio transport).",
    )
```

Add to `PanelAppDataConfigModel`:

```python
    gene_batch_cap: int = Field(
        default=20, ge=1, le=100,
        description="Max gene symbols per get_panels_for_genes call (upstream politeness).",
    )
```

Add to `ServerSettings` (after the `data` field):

```python
    otel: OtelConfigModel = Field(
        default_factory=OtelConfigModel, description="OpenTelemetry tracing configuration"
    )
```

- [ ] **Step 4: Implement `setup_tracing`**

In `panelapp_link/observability/tracing.py`, add at the bottom (and `import logging`, `import sys`, `from panelapp_link.config import settings`, `logger = logging.getLogger(__name__)`):

```python
def setup_tracing() -> bool:
    """Install an OTLP TracerProvider when PANELAPP_LINK_OTEL__ENABLED is set.

    No-op (returns False) when disabled or when the SDK/exporter is not
    installed. The console exporter is stderr-only and suppressed under stdio so
    it can never corrupt the MCP JSON-RPC channel.
    """
    if not settings.otel.enabled:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL enabled but opentelemetry-sdk/exporter missing; tracing stays no-op")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": "panelapp-link"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if settings.otel.console and settings.transport != "stdio":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing enabled (OTLP)")
    return True
```

- [ ] **Step 5: Wire into both transports**

In `panelapp_link/server_manager.py` `_lifespan`, after `configure_logging()`:

```python
    from panelapp_link.observability.tracing import setup_tracing

    setup_tracing()
```

In `start_stdio_server`, before `create_panelapp_mcp()`:

```python
        from panelapp_link.observability.tracing import setup_tracing

        setup_tracing()
```

- [ ] **Step 6: Add the optional dependency extra**

In `pyproject.toml`, after `[project.urls]` (or before it), add:

```toml
[project.optional-dependencies]
otel = [
    "opentelemetry-sdk>=1.20.0,<2.0.0",
    "opentelemetry-exporter-otlp>=1.20.0,<2.0.0",
]
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_tracing_setup.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml panelapp_link/config.py panelapp_link/observability/tracing.py panelapp_link/server_manager.py tests/test_tracing_setup.py
git commit -m "$(printf 'feat(observability): opt-in OTLP tracing bootstrap (stdio-safe)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 12: WS-3 wiring + WS-6c — gene cap from config, version bump, full CI

**Files:**
- Modify: `panelapp_link/mcp/tools/aggregations.py` (pass `cap` from settings)
- Modify: `pyproject.toml` (`version = "0.3.0"`)
- Modify: `panelapp_link/__init__.py` (fallback `__version__ = "0.3.0"`)
- Test: `tests/test_version_and_cap.py` (new)

**Interfaces:**
- Consumes: `settings.data.gene_batch_cap`.
- Produces: `get_panels_for_genes` honours the configured cap; capabilities reports `server_version 0.3.0`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_version_and_cap.py`:

```python
from __future__ import annotations

from panelapp_link.mcp.capabilities import server_version


def test_server_version_is_0_3_0() -> None:
    assert server_version() == "0.3.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version_and_cap.py -v`
Expected: FAIL — version is 0.2.0.

- [ ] **Step 3: Pass the configured cap through the tool**

In `panelapp_link/mcp/tools/aggregations.py`, change the `panels_for_genes` call inside `get_panels_for_genes`:

```python
            from panelapp_link.config import settings

            payload = await aggregations.panels_for_genes(
                get_panelapp_service(), gene_symbols,
                region=region, min_confidence=min_confidence, response_mode=response_mode,
                cap=settings.data.gene_batch_cap,
            )
```

- [ ] **Step 4: Bump the version (both sources)**

In `pyproject.toml`: `version = "0.3.0"`.
In `panelapp_link/__init__.py`, change the fallback assignment to `__version__ = "0.3.0"`.

- [ ] **Step 5: Reinstall so package metadata reflects 0.3.0, then run test**

Run: `uv sync && uv run pytest tests/test_version_and_cap.py tests/test_capabilities_tools.py -v`
Expected: PASS (`server_version()` reads `importlib.metadata.version` → 0.3.0; 9 tools).

- [ ] **Step 6: Full CI gate**

Run: `make ci-local`
Expected: format clean, lint clean, `lint-loc` clean (every module ≤ 600; `panelapp_service.py` unchanged), mypy clean, all tests pass, coverage ≥ 85%.

If `aggregations.py` or any touched file exceeds 600 lines, split the offending helper into `services/_aggregation_helpers.py` (pure functions) and re-run.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml panelapp_link/__init__.py panelapp_link/mcp/tools/aggregations.py tests/test_version_and_cap.py
git commit -m "$(printf 'feat: gene-batch cap from config; bump server_version to 0.3.0\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 13: WS-4b — Trim the existing tool descriptions

**Files:**
- Modify: `panelapp_link/mcp/tools/panels.py` (`search_panels`, `get_panel`, `get_panel_genes` descriptions)
- Modify: `panelapp_link/mcp/tools/genes.py` (`get_gene_panels` description)
- Test: `tests/test_tool_descriptions.py` (new)

**Interfaces:**
- Produces: shorter per-tool descriptions (the per-request token tax); the full workflow guidance still lives in capabilities + `panelapp://usage` (asserted in Task 10). `resolve_gene` was already reworded in Task 2 and is left as-is.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_descriptions.py`:

```python
from __future__ import annotations

from fastmcp import Client

from panelapp_link.mcp.facade import create_panelapp_mcp

_TRIMMED = ("search_panels", "get_panel", "get_panel_genes", "get_gene_panels")


async def test_descriptions_are_concise_but_keep_gotchas() -> None:
    async with Client(create_panelapp_mcp()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in _TRIMMED:
        assert len(tools[name].description) <= 320, name
    # Key gotchas preserved:
    assert "both" in tools["get_panel"].description.lower()
    assert "optional" in tools["get_gene_panels"].description.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_descriptions.py -v`
Expected: FAIL — current descriptions exceed 320 chars.

- [ ] **Step 3: Trim the four descriptions**

`panelapp_link/mcp/tools/panels.py` — replace each `description=(...)`:

```python
        # search_panels
        description=(
            "Search PanelApp panels by name, relevant disorders, or disease group "
            "across UK + Australia (region='both' default), deduped and ranked. Use "
            "it to find a panel_id, then page via _meta.next_commands."
        ),
```
```python
        # get_panel
        description=(
            "Return one panel's detail plus its entity-count breakdown. region must "
            "be a single concrete region ('uk' or 'australia'), not 'both'."
        ),
```
```python
        # get_panel_genes
        description=(
            "Return a panel's entities (genes by default; or region | str | all), "
            "filtered by min_confidence (green = green only; amber = amber+green; "
            "red = all). region must be concrete; widen response_mode for "
            "phenotypes/evidence."
        ),
```

`panelapp_link/mcp/tools/genes.py` — replace `get_gene_panels` description:

```python
        description=(
            "Return every panel a gene appears on across regions, sorted by "
            "confidence. Query by gene_symbol (required); hgnc_id is an OPTIONAL "
            "result filter, not a standalone query."
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_descriptions.py tests/test_capabilities_tools.py -v`
Expected: PASS (descriptions trimmed; capabilities/usage still carry guidance).

- [ ] **Step 5: Commit**

```bash
git add panelapp_link/mcp/tools/panels.py panelapp_link/mcp/tools/genes.py tests/test_tool_descriptions.py
git commit -m "$(printf 'perf(mcp): trim per-tool descriptions; guidance stays in capabilities\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 14: Docs + final smoke

**Files:**
- Modify: `docs/architecture.md`, `docs/usage.md` (document the two tools, `confidence_counts`, OTel opt-in, lean minimal `_meta`)
- Modify: `CHANGELOG.md` (0.3.0 entry)

- [ ] **Step 1: Update docs**

Add a "compare_panels / get_panels_for_genes" subsection to `docs/usage.md` with one worked example each; note `confidence_counts` on `get_panel` (standard+), the lean minimal `_meta`, and `PANELAPP_LINK_OTEL__ENABLED` / `PANELAPP_LINK_DATA__GENE_BATCH_CAP`. Add a 0.3.0 section to `CHANGELOG.md` summarizing M1 fix, two tools, lean meta, OTel, confidence_counts.

- [ ] **Step 2: Live smoke (manual, optional — needs network)**

```bash
uv run python - <<'PY'
import asyncio, json
from fastmcp import Client
from panelapp_link.mcp.facade import create_panelapp_mcp
async def main():
    async with Client(create_panelapp_mcp()) as c:
        r = await c.call_tool("compare_panels", {"panels":[{"panel_id":283,"region":"uk"},{"panel_id":487,"region":"uk"}]}, raise_on_error=False)
        print(json.dumps(r.structured_content["summary"]))
asyncio.run(main())
PY
```
Expected: a `{"n_shared": ..., "n_union": ...}` summary.

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md docs/usage.md CHANGELOG.md
git commit -m "$(printf 'docs: document v0.3.0 aggregation tools, confidence_counts, OTel\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Done criteria
- `make ci-local` green; coverage ≥ 85%; all modules ≤ 600; `panelapp_service.py` byte-unchanged.
- `get_server_capabilities` → 9 tools, `server_version 0.3.0`.
- M1 gone (n_* in all modes; no `number_of_*`); `confidence_counts` additive; minimal `_meta` lean; OTLP tracing exports when enabled (no-op + stdio-safe otherwise).
