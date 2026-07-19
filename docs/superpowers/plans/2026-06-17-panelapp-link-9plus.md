# PanelApp-Link "Beyond 9/10" Implementation Plan

> Historical record — this plan records the proposed implementation as of its date. Current
> behavior is defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the already-built observability/cache/rate-limit work and close the remaining contract (B-1), search-quality (B-2), and recovery (B-3) gaps so the *deployed* `panelapp-link` MCP server earns a 10/10.

**Architecture:** Surgical edits to existing modules — fix one breadcrumb + tool/contract wording (B-1), make panel search word-boundary aware and ranked (B-2), stop a slow-path recovery nudge (B-3) — on top of committing the in-flight infrastructure (single-flight cache, OTel/RED metrics, rate limiting) that is already wired but uncommitted and undeployed. Prewarm is enabled via deployment env, not a library default, to preserve the offline posture.

**Tech Stack:** Python 3.12, FastMCP 3.x + `mcp` 1.27, FastAPI/uvicorn, httpx, structlog, OpenTelemetry API, pytest + respx, ruff + mypy (strict), `uv`. Hard rule: **≤600 lines per module** (`make lint-loc`); `panelapp_service.py` is at 577/600 — keep new logic in `_live_helpers.py` / `next_commands.py`.

**Spec:** `docs/superpowers/specs/2026-06-17-panelapp-link-9plus-design.md`

---

## Task 1: Branch + commit the in-flight infrastructure baseline

The observability/cache/rate-limit work and prod-compose env exist in the working tree but are uncommitted. Land them first as a clean baseline so later fix-commits are isolated and reviewable.

**Files:**
- Create (commit): the spec + this plan under `docs/superpowers/`
- Commit (untracked): `panelapp_link/observability/`, `panelapp_link/services/cache.py`, `panelapp_link/mcp/rate_limit.py`, `tests/test_cache.py`, `tests/test_envelope_observability.py`, `tests/test_metrics.py`, `tests/test_rate_limit.py`, `tests/test_service_observability.py`, `tests/test_tracing.py`
- Commit (modified): `panelapp_link/mcp/{envelope,capabilities,facade,schemas}.py`, `panelapp_link/mcp/tools/discovery.py`, `panelapp_link/server_manager.py`, `panelapp_link/services/panelapp_service.py`, `panelapp_link/config.py`, `pyproject.toml`, `uv.lock`, `docker/docker-compose.prod.yml`, `docs/architecture.md`, `AGENTS.md`, `tests/test_app.py`, `tests/test_tools.py`

- [ ] **Step 1: Create the feature branch**

`main` is the default branch — never commit fixes directly to it.

Run:
```bash
cd /home/bernt-popp/development/panelapp-link
git checkout -b feat/panelapp-link-9plus
```
Expected: `Switched to a new branch 'feat/panelapp-link-9plus'` (uncommitted changes carry over).

- [ ] **Step 2: Confirm the in-flight tree is green BEFORE committing**

Run:
```bash
make ci-local
```
Expected: PASS (format, lint, lint-loc, mypy, tests, coverage ≥85%). If it fails, stop and fix the in-flight work before proceeding — do not commit a red baseline.

- [ ] **Step 3: Commit the spec + plan docs**

```bash
git add docs/superpowers/specs/2026-06-17-panelapp-link-9plus-design.md \
        docs/superpowers/plans/2026-06-17-panelapp-link-9plus.md
git commit -m "docs: spec + plan for panelapp-link 9plus improvements

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Commit the in-flight infrastructure**

Everything else uncommitted is the observability/cache/rate-limit/prod-compose work.

```bash
git add -A
git commit -m "feat(observability): ship single-flight cache, OTel/RED metrics, MCP rate limiting

Lands the in-flight infrastructure: RequestCache single-flight coalescing
(services/cache.py), OpenTelemetry API spans + RED metrics + /metrics endpoint
+ per-request _meta telemetry (observability/), opt-in token-bucket rate limiting
(mcp/rate_limit.py), and prod-compose prewarm/refresh/rate-limit env.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Verify a clean tree**

Run:
```bash
git status --short
```
Expected: empty output (all changes committed).

---

## Task 2: B-1 — fix the `resolve_gene → get_gene_panels` breadcrumb

`after_resolve_gene` emits `get_gene_panels(hgnc_id=…)`, but `get_gene_panels` requires `gene_symbol`, so following the breadcrumb fails with `invalid_input`. The resolved gene always carries `gene_symbol` — emit that.

**Files:**
- Modify: `panelapp_link/mcp/next_commands.py:48-56`
- Test: `tests/test_next_commands.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_next_commands.py`:
```python
def test_after_resolve_gene_emits_gene_symbol_not_hgnc() -> None:
    from panelapp_link.mcp.next_commands import after_resolve_gene

    gene = {"gene_symbol": "PKD1", "hgnc_id": "HGNC:9008"}
    assert after_resolve_gene(gene) == [
        {"tool": "get_gene_panels", "arguments": {"gene_symbol": "PKD1"}}
    ]


def test_after_resolve_gene_empty_without_symbol() -> None:
    from panelapp_link.mcp.next_commands import after_resolve_gene

    # hgnc_id alone cannot drive get_gene_panels, so no breadcrumb is offered.
    assert after_resolve_gene({"hgnc_id": "HGNC:9008"}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_next_commands.py::test_after_resolve_gene_emits_gene_symbol_not_hgnc -v
```
Expected: FAIL — current code returns `{"hgnc_id": "HGNC:9008"}` arguments.

- [ ] **Step 3: Implement the fix**

In `panelapp_link/mcp/next_commands.py`, replace the `after_resolve_gene` function (lines 48-56):
```python
def after_resolve_gene(gene: dict[str, Any]) -> list[dict[str, Any]]:
    """After resolving a gene: list every panel it appears on across regions.

    PanelApp is queried by gene symbol, so the breadcrumb passes ``gene_symbol``
    (the canonical query key). ``hgnc_id`` is only an optional result filter and
    cannot drive ``get_gene_panels`` on its own, so it is never emitted alone.
    """
    symbol = gene.get("gene_symbol")
    if symbol:
        return [cmd("get_gene_panels", gene_symbol=symbol)]
    return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_next_commands.py -v
```
Expected: PASS (both new tests + existing).

- [ ] **Step 5: Strengthen the existing tool test**

In `tests/test_tools.py`, the `test_resolve_gene_success` test asserts only the breadcrumb tool name. Add an arguments assertion. Replace the line:
```python
    assert data["_meta"]["next_commands"][0]["tool"] == "get_gene_panels"
```
with:
```python
    breadcrumb = data["_meta"]["next_commands"][0]
    assert breadcrumb["tool"] == "get_gene_panels"
    assert breadcrumb["arguments"] == {"gene_symbol": "AAAS"}
```

- [ ] **Step 6: Run the tool test**

Run:
```bash
uv run pytest tests/test_tools.py::test_resolve_gene_success -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add panelapp_link/mcp/next_commands.py tests/test_next_commands.py tests/test_tools.py
git commit -m "fix(B-1): emit gene_symbol breadcrumb after resolve_gene

resolve_gene's next_command pointed get_gene_panels at hgnc_id, which the tool
rejects (it queries by symbol). Emit the always-present gene_symbol so following
the breadcrumb succeeds.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: B-1 — align the `hgnc_id` contract wording

The tool description, capabilities `parameter_conventions`, and `recommended_workflows` advertise `hgnc_id` as a co-equal query identifier. Reword to: `gene_symbol` drives the query; `hgnc_id` is an optional disambiguation filter.

**Files:**
- Modify: `panelapp_link/mcp/tools/genes.py:42-49`
- Modify: `panelapp_link/mcp/capabilities.py:82-94`
- Test: `tests/test_capabilities.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capabilities.py`:
```python
def test_capabilities_hgnc_is_filter_not_query_key() -> None:
    from panelapp_link.mcp.capabilities import build_capabilities

    caps = build_capabilities()
    conv = caps["parameter_conventions"]
    # hgnc_id must no longer claim to be a stand-alone, mutually-exclusive identifier.
    assert "mutually exclusive" not in conv["hgnc_id"].lower()
    assert "filter" in conv["hgnc_id"].lower()
    assert "mutually exclusive" not in conv["gene_symbol"].lower()
```

> Note: if the public builder is named differently, discover it first with
> `uv run python -c "import panelapp_link.mcp.capabilities as c; print([n for n in dir(c) if 'capab' in n.lower()])"`
> and use that name. `test_capabilities.py:35` already calls the builder — match its import.

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
uv run pytest tests/test_capabilities.py::test_capabilities_hgnc_is_filter_not_query_key -v
```
Expected: FAIL — current text says "mutually exclusive with gene_symbol".

- [ ] **Step 3: Reword the `get_gene_panels` tool description**

In `panelapp_link/mcp/tools/genes.py`, replace the `description=(...)` block of the `get_gene_panels` tool (lines 42-49):
```python
        description=(
            "Return every PanelApp panel a gene appears on, across regions, sorted "
            "by confidence (green > amber > red) then region. Query by gene_symbol "
            "(the approved symbol, e.g. BRCA1) -- this is the required query key. "
            "hgnc_id (HGNC CURIE, e.g. HGNC:1100) is an OPTIONAL disambiguation "
            "filter applied to the results, not a stand-alone query; pass it "
            "alongside gene_symbol. region='both' (default) spans UK + Australia; "
            "min_confidence floors the traffic-light rank (green = green only). Use "
            "resolve_gene first if a free-text symbol is uncertain."
        ),
```

- [ ] **Step 4: Reword capabilities `parameter_conventions` + `recommended_workflows`**

In `panelapp_link/mcp/capabilities.py`, replace the two `recommended_workflows` lines that mention `hgnc_id` (lines 85 and 88):
```python
            "gene symbol -> resolve_gene -> get_gene_panels(gene_symbol=...)",
```
```python
            "compare a gene across regions -> get_gene_panels(gene_symbol=..., region='both')",
```

Then replace the `gene_symbol` and `hgnc_id` entries in `parameter_conventions` (lines 93-94):
```python
            "gene_symbol": "approved gene symbol (e.g. BRCA1); the query key for "
            "resolve_gene / get_gene_panels",
            "hgnc_id": "HGNC CURIE (e.g. HGNC:1100); OPTIONAL disambiguation filter "
            "for get_gene_panels results -- gene_symbol drives the query",
```

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
uv run pytest tests/test_capabilities.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/tools/genes.py panelapp_link/mcp/capabilities.py tests/test_capabilities.py
git commit -m "fix(B-1): align hgnc_id contract wording (filter, not query key)

Description, parameter_conventions, and recommended_workflows now state that
gene_symbol is the query key and hgnc_id is an optional result filter, matching
the service behaviour.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: B-2 — word-boundary, ranked panel matching helpers

Replace naive substring matching (`"renal"` matches `"adrenal"`) with per-token word-prefix matching plus a field-weighted relevance score.

**Files:**
- Modify: `panelapp_link/services/_live_helpers.py` (add `import re`; replace `panel_matches`; add `panel_match_score`, `rank_panels`)
- Test: `tests/test_live_helpers.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_helpers.py`:
```python
"""Unit tests for the pure live-service helpers (matching + ranking)."""

from __future__ import annotations

from panelapp_link.services import _live_helpers as helpers


def test_score_word_prefix_not_substring() -> None:
    # "renal" is a mid-word substring of "Adrenal" -> must NOT match.
    assert helpers.panel_match_score({"name": "Adrenal insufficiency"}, "renal") == 0
    # whole-word prefix match on the name field -> weight 3.
    assert helpers.panel_match_score({"name": "Renal ciliopathies"}, "renal") == 3


def test_score_field_weighting() -> None:
    by_name = {"name": "Cystic kidney disease"}
    by_disorder = {"name": "X", "relevant_disorders": ["cystic kidney disease"]}
    by_group = {"name": "X", "disease_group": "Cystic disorders"}
    assert helpers.panel_match_score(by_name, "cystic") == 3
    assert helpers.panel_match_score(by_disorder, "cystic") == 2
    assert helpers.panel_match_score(by_group, "cystic") == 1


def test_score_multi_token_requires_all_in_one_field() -> None:
    panel = {"name": "Cystic kidney disease"}
    assert helpers.panel_match_score(panel, "cystic kidney") == 3
    assert helpers.panel_match_score(panel, "cystic lung") == 0


def test_panel_matches_is_score_gt_zero() -> None:
    assert helpers.panel_matches({"name": "Renal ciliopathies"}, "renal") is True
    assert helpers.panel_matches({"name": "Adrenal insufficiency"}, "renal") is False


def test_rank_panels_orders_name_match_first() -> None:
    rows = [
        {"name": "Z disorder", "relevant_disorders": ["cystic kidney"], "region": "uk"},
        {"name": "Cystic kidney disease", "relevant_disorders": [], "region": "uk"},
    ]
    ranked = helpers.rank_panels(rows, "cystic")
    assert ranked[0]["name"] == "Cystic kidney disease"


def test_rank_panels_empty_needle_is_alphabetical() -> None:
    rows = [{"name": "B", "region": "uk"}, {"name": "A", "region": "uk"}]
    assert [r["name"] for r in helpers.rank_panels(rows, "")] == ["A", "B"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_live_helpers.py -v
```
Expected: FAIL — `panel_match_score` / `rank_panels` do not exist yet.

- [ ] **Step 3: Implement the helpers**

In `panelapp_link/services/_live_helpers.py`, add `import re` to the imports (after `from typing import Any`):
```python
import re
```

Then replace the existing `panel_matches` function (lines 29-37) with:
```python
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: Any) -> list[str]:
    """Lowercase alphanumeric word tokens of a value (``[]`` for blanks/None)."""
    return _TOKEN_RE.findall(str(text or "").lower())


def _weighted_fields(panel: dict[str, Any]) -> list[tuple[int, str]]:
    """(weight, text) searchable fields; higher weight = more relevant field."""
    fields: list[tuple[int, str]] = [(3, str(panel.get("name") or ""))]
    fields += [(2, str(d)) for d in (panel.get("relevant_disorders") or [])]
    fields.append((1, str(panel.get("disease_group") or "")))
    fields.append((1, str(panel.get("disease_sub_group") or "")))
    return fields


def panel_match_score(panel: dict[str, Any], needle: str) -> int:
    """Relevance score for ``needle`` vs a panel (0 = no match).

    Every query token must word-prefix-match a *whole word* within a single
    searchable field (so ``renal`` does not match ``adrenal``). The score is the
    best matching field's weight: name (3) > relevant_disorders (2) > disease
    group/sub-group (1).
    """
    q_tokens = _tokens(needle)
    if not q_tokens:
        return 0
    best = 0
    for weight, text in _weighted_fields(panel):
        words = _tokens(text)
        if words and all(any(w.startswith(qt) for w in words) for qt in q_tokens):
            best = max(best, weight)
    return best


def panel_matches(panel: dict[str, Any], needle: str) -> bool:
    """True when ``needle`` matches a panel by word-prefix (see panel_match_score)."""
    return panel_match_score(panel, needle) > 0


def rank_panels(rows: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    """Sort normalized panel rows: relevance desc, then name, then region.

    An empty ``needle`` preserves the prior alphabetical (name, region) order.
    """
    if not (needle or "").strip():
        return sorted(rows, key=lambda p: ((p.get("name") or "").lower(), p.get("region") or ""))
    return sorted(
        rows,
        key=lambda p: (
            -panel_match_score(p, needle),
            (p.get("name") or "").lower(),
            p.get("region") or "",
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_live_helpers.py -v
```
Expected: PASS (all 6 tests).

- [ ] **Step 5: Verify the file is within the line budget**

Run:
```bash
wc -l panelapp_link/services/_live_helpers.py
```
Expected: well under 600 (≈140).

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/services/_live_helpers.py tests/test_live_helpers.py
git commit -m "feat(B-2): word-boundary matching + field-weighted ranking helpers

panel_match_score does per-token word-prefix matching (renal no longer matches
adrenal) and weights name > disorders > disease group; rank_panels sorts by
relevance. panel_matches now delegates to the score.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: B-2 — wire ranking into `search_panels`

Swap the alphabetical sort for relevance ranking. The filter at line 279 already calls `helpers.panel_matches`, which now uses the word-prefix logic — so the only change is the sort.

**Files:**
- Modify: `panelapp_link/services/panelapp_service.py:284`
- Test: `tests/test_service.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service.py`:
```python
async def test_search_panels_word_prefix_not_substring(live_service) -> None:
    # "porphyria" is a whole word in "Acute intermittent porphyria".
    hit = await live_service.search_panels(query="porphyria", region="uk")
    assert hit["total"] >= 1
    # "orphyr" is only a mid-word substring -> must NOT match under word-prefix rules.
    miss = await live_service.search_panels(query="orphyr", region="uk")
    assert miss["total"] == 0
```

> Note: tests in this suite are `async def` and use the `live_service` fixture from
> `tests/conftest.py` (respx-mocked). Match the existing import/marker style at the
> top of `tests/test_service.py`.

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
uv run pytest tests/test_service.py::test_search_panels_word_prefix_not_substring -v
```
Expected: FAIL on the `miss` assertion if any stale build is loaded, or PASS-by-accident only after Task 4. If it already passes (because `panel_matches` was committed in Task 4), continue — Step 3 still wires ranking, covered by Step 4's ordering check.

- [ ] **Step 3: Wire `rank_panels` into the sort**

In `panelapp_link/services/panelapp_service.py`, replace line 284:
```python
        normalized.sort(key=lambda p: ((p.get("name") or "").lower(), p.get("region") or ""))
```
with:
```python
        normalized = helpers.rank_panels(normalized, needle)
```

- [ ] **Step 4: Add an ordering regression test**

Append to `tests/test_service.py`:
```python
async def test_search_panels_ranks_results_by_relevance(live_service) -> None:
    from panelapp_link.services import _live_helpers as helpers

    out = await live_service.search_panels(query="acute", region="uk", limit=50)
    scores = [helpers.panel_match_score(p, "acute") for p in out["panels"]]
    # rank_panels must return scores in non-increasing order.
    assert scores == sorted(scores, reverse=True)
    assert scores and scores[0] > 0
```

- [ ] **Step 5: Run both service tests to verify they pass**

Run:
```bash
uv run pytest tests/test_service.py::test_search_panels_word_prefix_not_substring tests/test_service.py::test_search_panels_ranks_results_by_relevance -v
```
Expected: PASS.

- [ ] **Step 6: Confirm the service file is still within budget**

Run:
```bash
wc -l panelapp_link/services/panelapp_service.py
```
Expected: ≤600 (net change is 0 — one line swapped for one line).

- [ ] **Step 7: Commit**

```bash
git add panelapp_link/services/panelapp_service.py tests/test_service.py
git commit -m "fix(B-2): rank search_panels results by relevance

search_panels now orders by rank_panels (name > disorders > disease group, then
name/region) instead of plain alphabetical; word-prefix filtering drops mid-word
substring false positives.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: B-3 — drop the slow-path recovery nudge for panel `not_found`

On `get_panel` / `get_panel_genes` `not_found`, the recovery breadcrumb is `search_panels(query="")`, which triggers the heavy full-list pull. A bad `panel_id` has no name to search — emit no breadcrumb (the prose `recovery_action: switch_tool` already conveys intent).

**Files:**
- Modify: `panelapp_link/mcp/next_commands.py:99-101`
- Test: `tests/test_next_commands.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_next_commands.py`:
```python
def test_recovery_panel_not_found_emits_no_empty_search() -> None:
    from panelapp_link.mcp.next_commands import recovery_commands

    for tool in ("get_panel", "get_panel_genes"):
        nexts = recovery_commands(tool, "not_found", {"panel_id": 999, "region": "uk"}, None)
        assert nexts == []


def test_recovery_gene_not_found_still_suggests_resolve() -> None:
    from panelapp_link.mcp.next_commands import recovery_commands

    nexts = recovery_commands("get_gene_panels", "not_found", {"gene_symbol": "ZZZ"}, None)
    assert nexts == [{"tool": "resolve_gene", "arguments": {"query": "ZZZ"}}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_next_commands.py::test_recovery_panel_not_found_emits_no_empty_search -v
```
Expected: FAIL — current code returns `[{"tool": "search_panels", "arguments": {"query": ""}}]`.

- [ ] **Step 3: Implement the fix**

In `panelapp_link/mcp/next_commands.py`, in `recovery_commands`, replace the `not_found` branch (lines 99-105) — change the `get_panel` / `get_panel_genes` case to emit nothing:
```python
    if error_code == "not_found":
        if tool == "resolve_gene" and gene_in:
            nexts = [cmd("search_panels", query=gene_in)]
        elif tool == "get_gene_panels" and gene_in:
            nexts = [cmd("resolve_gene", query=gene_in)]
        # get_panel / get_panel_genes: a bad panel_id has nothing to search for, and
        # search_panels(query="") triggers the heavy full-list pull -- offer nothing.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_next_commands.py -v
```
Expected: PASS (new + existing).

- [ ] **Step 5: Check for an existing test asserting the old behavior**

Run:
```bash
grep -rn 'search_panels.*query.*""' tests/
```
Expected: no test asserts the removed empty-query recovery. If one exists (e.g. in `tests/test_envelope.py`), update it to assert `[]` for the panel `not_found` case, then re-run that file.

- [ ] **Step 6: Commit**

```bash
git add panelapp_link/mcp/next_commands.py tests/test_next_commands.py
git commit -m "fix(B-3): no empty-query recovery on panel not_found

search_panels(query='') triggers the heavy full-list pull; a bad panel_id has
nothing to search for. Emit no breadcrumb for get_panel/get_panel_genes
not_found; gene-tool recoveries are unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: WS-E — verify deployment prewarm + lifespan regression test

No library default change (offline-safe). Verify the prod compose enables prewarm/refresh, and add a regression test that the lifespan prewarm path warms the cache when enabled, using respx (no real network).

**Files:**
- Verify: `docker/docker-compose.prod.yml`
- Modify: `panelapp_link/config.py` (assert defaults stay offline-safe — no change expected)
- Test: `tests/test_service.py` (append a prewarm test)

- [ ] **Step 1: Verify prod compose enables prewarm/refresh/rate-limit**

Run:
```bash
grep -n "PREWARM\|REFRESH_INTERVAL\|RATE_LIMIT_PER_MINUTE" docker/docker-compose.prod.yml
```
Expected: `PANELAPP_LINK_DATA__PREWARM: "true"`, `PANELAPP_LINK_DATA__REFRESH_INTERVAL: "3600"`, `PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE: "120"` all present. If any is missing, add it under the `environment:` block.

- [ ] **Step 2: Confirm the library default stays offline-safe**

Run:
```bash
uv run python -c "from panelapp_link.config import PanelAppDataConfigModel as M; m=M(); print('prewarm', m.prewarm); print('refresh', m.refresh_interval)"
```
Expected: `prewarm False`, `refresh 0`. (If not, this contradicts the offline posture — stop and reconcile.)

- [ ] **Step 3: Write the failing prewarm test**

Append to `tests/test_service.py`:
```python
async def test_prewarm_populates_cache(live_service) -> None:
    # Cold cache: nothing stored yet.
    assert live_service._cache.stats()["entries"] == 0
    await live_service.prewarm()
    # Prewarm fetched the heavy panel + signed-off lists for both regions.
    assert live_service._cache.stats()["entries"] > 0
    # A subsequent search is now served from cache (no further upstream needed).
    out = await live_service.search_panels(query="acute", region="uk")
    assert out["total"] >= 1
```

> Note: if the cache attribute is not `_cache` (confirm with
> `grep -n "self\._cache\|RequestCache\|def stats" panelapp_link/services/panelapp_service.py`),
> use the actual attribute / a public `cache_stats()` accessor if one exists.

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
uv run pytest tests/test_service.py::test_prewarm_populates_cache -v
```
Expected: PASS (prewarm already implemented; this locks the behavior in).

- [ ] **Step 5: Commit**

```bash
git add tests/test_service.py
git commit -m "test(WS-E): lock prewarm cache-warming behavior

Regression test that service.prewarm() populates the cache so the first
search_panels is a hit. Prewarm stays enabled via prod-compose env; library
defaults remain offline-safe.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Version bump to 0.2.0 + docs prose

Mark the new observability surface. `server_version` is read from package metadata, so bump `pyproject.toml` and the in-package fallback, then reinstall so the running server reports 0.2.0.

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `panelapp_link/__init__.py:11`
- Modify: `docs/architecture.md` (hgnc_id wording, if present)

- [ ] **Step 1: Bump the package version**

In `pyproject.toml`, change line 7:
```toml
version = "0.2.0"
```

In `panelapp_link/__init__.py`, change the fallback (line 11):
```python
    __version__ = "0.2.0"
```

- [ ] **Step 2: Reinstall so metadata reflects the bump**

Run:
```bash
uv sync
```
Expected: environment re-synced; `panelapp-link` metadata now reports 0.2.0.

- [ ] **Step 3: Verify the reported version**

Run:
```bash
uv run python -c "from panelapp_link.mcp.capabilities import server_version; print(server_version())"
```
Expected: `0.2.0`.

- [ ] **Step 4: Update architecture docs for the hgnc_id contract**

Run:
```bash
grep -n "hgnc" docs/architecture.md
```
If any line describes `hgnc_id` as a co-equal/alternative query identifier for `get_gene_panels`, reword it to: "`gene_symbol` is the query key; `hgnc_id` is an optional disambiguation filter." If there is no such mention, skip this edit.

- [ ] **Step 5: Run the capabilities/version tests**

Run:
```bash
uv run pytest tests/test_capabilities.py tests/test_app.py -v
```
Expected: PASS (`test_capabilities.py:35` only asserts truthiness; no test pins 0.1.0 — if one does, update it to 0.2.0).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml panelapp_link/__init__.py uv.lock docs/architecture.md
git commit -m "chore: bump to 0.2.0 for observability + contract fixes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full verification, container build, deploy check

**Files:** none (verification only)

- [ ] **Step 1: Run the full local CI gate**

Run:
```bash
make ci-local
```
Expected: PASS — format, lint, **lint-loc** (all modules ≤600), mypy strict, full test suite, coverage ≥85%.

- [ ] **Step 2: Build the container**

Run:
```bash
make docker-build
```
Expected: image builds successfully.

- [ ] **Step 3: Bring up the prod-config stack locally**

Run:
```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```
Then wait for health and check the metrics + version surface:
```bash
sleep 20
curl -fs http://127.0.0.1:8000/health
curl -fs http://127.0.0.1:8000/metrics | head -20
```
Expected: `/health` returns `{"status":"ok",...,"version":"0.2.0",...}`; `/metrics` returns Prometheus `text/plain` RED metrics (request/cache/upstream series).

> Note: `docker-compose.prod.yml` sets `ports: !reset []` (no published ports). For
> this local smoke check, publish 8000 via an inline override:
> `docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml run --service-ports ...`
> or temporarily add `-p` mapping. In real production it sits behind a reverse proxy.

- [ ] **Step 4: Smoke-test the live MCP surface**

With the stack up, confirm the deployed envelope now carries telemetry. Call a tool through the MCP endpoint (or, if simpler, via the FastMCP client used in `tests/test_tools.py`) and assert the response `_meta` contains `cache` and `upstream_ms`, and that prewarm made `search_panels` fast:
```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml logs panelapp-link | grep -i "prewarm"
```
Expected: a "prewarming panel lists" log line at boot (proves prewarm ran). The first `search_panels` after boot should be a cache hit (sub-second `elapsed_ms`).

- [ ] **Step 5: Tear down the local stack**

Run:
```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml down
```

- [ ] **Step 6: Push the branch and open a PR**

```bash
git push -u origin feat/panelapp-link-9plus
gh pr create --title "PanelApp-Link: beyond 9/10 (ship observability + fix B-1/B-2/B-3)" --body "$(cat <<'EOF'
Ships the in-flight observability/single-flight-cache/rate-limit work and closes the
remaining black-box findings.

- **B-1 (High):** resolve_gene breadcrumb now emits gene_symbol; hgnc_id contract
  reworded to "optional filter" across description/capabilities/workflows.
- **B-2:** word-boundary matching (renal no longer matches adrenal) + relevance ranking.
- **B-3:** no empty-query (slow full-list) recovery nudge on panel not_found.
- **WS-E:** prewarm/refresh/rate-limit enabled via prod-compose env (offline library default kept).
- Version bumped to 0.2.0.

Spec: docs/superpowers/specs/2026-06-17-panelapp-link-9plus-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

> **Production redeploy** (pulling the new image to the actual host) depends on the
> deployment environment and is performed by the operator after the PR merges.

---

## Self-Review

**Spec coverage:**
- WS-A (ship infra) → Task 1.
- WS-B (B-1 contract) → Task 2 (breadcrumb) + Task 3 (wording).
- WS-C (B-2 search) → Task 4 (helpers) + Task 5 (wiring).
- WS-D (B-3 recovery) → Task 6.
- WS-E (prewarm deploy) → Task 7.
- Version bump + docs → Task 8.
- Quality gates + deploy verification → Task 9.
All spec sections map to tasks. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. The three `> Note:` callouts are explicit fallbacks (discover-the-real-name), not placeholders — each gives a concrete discovery command. ✓

**Type/name consistency:** `panel_match_score`, `panel_matches`, `rank_panels`, `after_resolve_gene`, `recovery_commands`, `cmd`, `_cache.stats()` are used consistently across tasks and match the current code read during planning. `rank_panels(normalized, needle)` operates on normalized rows that retain `name`/`region`/`relevant_disorders`/`disease_group`/`disease_sub_group`. ✓

**Line-budget guard:** Only `panelapp_service.py` (577/600) is touched, with a net-zero one-line swap (Task 5). New logic lives in `_live_helpers.py` (≈140 after Task 4) and `next_commands.py`. ✓
