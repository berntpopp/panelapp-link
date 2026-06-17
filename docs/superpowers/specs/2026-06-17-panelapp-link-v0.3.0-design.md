# PanelApp-Link — "Beyond 9/10" Phase 2 Spec (v0.3.0)

- **Date:** 2026-06-17
- **Status:** Approved (design) — rev. 2 (external Codex review incorporated)
- **Author:** Senior MCP engineering review (LLM-consumer perspective)
- **Rev. 2 changes (Codex review, all 7 findings verified against code & accepted):**
  (1) `confidence_counts` added as a sibling field — `entity_counts` stays integer
  (no second breaking change); (2) M1 guarded by explicit `n_*`-present /
  `number_of_*`-absent contract tests, since the schema is permissive
  (`additionalProperties:True`); (3) `compare_panels` sources panel metadata from
  `get_panel` (shared cache), not `get_panel_genes` (which omits `name`);
  (4) `get_panels_for_genes` isolates per-symbol `NotFoundError` → `not_found` while
  operational errors fail the envelope; (5) OTel: OTLP-only by default, console
  exporter is separately gated, stderr-only, and off under stdio (no JSON-RPC framing
  corruption); (6) explicit response-mode contracts defined for both new tools;
  (7) version bumped at `pyproject.toml` + `__init__.py` fallback, not `config.py`.
- **Supersedes context:** builds on `2026-06-17-panelapp-link-9plus-design.md` (v0.2.0,
  shipped in commit `4387cc4`).
- **Scope decision (user-locked):**
  - **Ambition:** *Capability expansion* — correctness fixes + token/observability
    depth + two new value-add tools.
  - **M1 fix:** *Normalize to `n_*` everywhere* (clean break, pre-1.0).
  - **Deployment target:** *Single-worker container* (in-process cache; OTel opt-in;
    **no** Redis / shared cache / shared rate limiter).

## 1. Background

The v0.2.0 effort (prior spec, commit `4387cc4`) already shipped: B-1 (`hgnc_id`
contract alignment), B-2 (word-aware ranked search), B-3 (recovery-nudge fix),
RED metrics + `/metrics`, OpenTelemetry **API-only** spans, single-flight cache,
opt-in MCP rate-limit, and deploy-layer prewarm. Working tree is clean — all of it
is committed and live.

A fresh black-box test of the **shipped** server (28 live calls driven through the
real `fastmcp.Client`, exercising every tool, all four response modes, pagination,
caching, and an 8-case negative matrix) scored it **8.5/10** and surfaced issues the
prior spec did not cover.

### New findings (this phase)

1. **🟠 M1 — output field rename across `response_mode` (contract instability).**
   `search_panels` and `get_panel` emit `n_genes / n_regions / n_strs` in
   minimal/compact/standard, but **`number_of_genes / number_of_regions /
   number_of_strs`** in `full` mode.
   - **Root cause:** `services/shaping.py:shape_panel` returns `dict(row)` verbatim
     for `full` (shaping.py:176-177); the normalized `row` carries upstream
     `number_of_*` names (documented at shaping.py:169). Every other mode remaps to
     `n_*` (shaping.py:183-185).
   - **Impact:** a programmatic consumer keyed on `n_genes` gets `undefined` the
     moment it widens verbosity. LLMs degrade gracefully; clients break. A
     response-mode must change *which* fields appear, never *rename* shared ones.
2. **🟡 L2 — doc/payload naming drift.** Capabilities and tool descriptions say
   *"strongest confidence"*; the `resolve_gene` payload field is
   `max_confidence_label`. No functional bug (it returns `green` correctly) — wording
   should match the key.
3. **L1 — retracted.** The absent `not_found` recovery `next_commands` on `get_panel`
   is **by design** (prior spec WS-D deliberately returns `[]` to avoid nudging into
   the slow full-list pull). Not a defect.

### Confirmed strengths (keep — do not regress)
Error taxonomy + recovery actions, domain validation beyond schema (`region='both'`
rejected with a tailored message), opaque-cursor pagination, single-flight cache
(cold 12.5 s → warm 3.4 ms on `search_panels`), `next_commands` navigation, correct
read-only/idempotent/open-world annotations, `capabilities_version` content hash.

## 2. Decisions (locked)

- **M1 = normalize to `n_*` in all modes.** Drop `number_of_*` from the `full`-mode
  panel payload. Add a regression test asserting shared count-field *names* are
  identical across all four modes.
- **Two new tools only** (7 → 9): `compare_panels` and `get_panels_for_genes`.
  Deliberately disciplined — the "limit tool count" best practice (fewer tools =
  easier selection, lower static schema tax) is respected by choosing only the two
  highest-leverage aggregation/batch tools and rejecting a broader tool sprawl.
- **Lean `_meta` via auto-trim in `minimal` mode** — no new per-tool parameter (avoids
  a param tax across every tool). The existing `next_commands` trim is extended to
  also drop `upstream`, `upstream_ms`, and `citation_short` in minimal mode.
- **OTel exporter is opt-in** behind `PANELAPP_LINK_OTEL__ENABLED`; default remains a
  no-op. Single-worker only; no collector assumptions baked in.
- **`server_version` → 0.3.0** (new tools = minor bump). `capabilities_version` is a
  content hash and rehashes automatically.
- **L2 = align prose to the field** (`max_confidence_label`), not a schema rename
  (renaming the field would itself break `resolve_gene` consumers).

## 3. Constraints

- **File-size budget: 600 lines/module**, enforced by `make lint-loc` (in `ci-local`).
  `panelapp_service.py` is at **580/600** — it must NOT grow. New orchestration goes
  in new modules.
- Public MCP tools stay **read-only, research-use scoped**.
- **Upstream politeness (AGENTS.md):** batch fan-out must reuse the existing
  concurrency cap (default 4), jittered backoff, `Retry-After`, and the in-memory
  cache. Never fan out unbounded — PanelApp rate-limits per-IP bursts with HTTP 429.
- Tests must not hit the network except under the `integration` marker; use `respx`
  with committed fixtures (`tests/fixtures/`, pattern in `tests/conftest.py`
  `build_router`).
- `make ci-local` must pass: format, lint, lint-loc, mypy (strict, 3.12), tests,
  coverage gate **≥85%**.

## 4. Workstreams

### WS-1 — Contract correctness (M1, L2) + schema-conformance guard

- **M1:** `services/shaping.py:shape_panel` — for `full` mode, build the output from
  `row` but remap the three count fields to `n_genes / n_regions / n_strs` (drop
  `number_of_*`). All four modes now expose identical shared field names.
- Verify `SEARCH_PANELS_SCHEMA` / `GET_PANEL_SCHEMA` (`mcp/schemas.py`) declare `n_*`
  (not `number_of_*`); fix if drifted.
- **L2:** reword `resolve_gene` description (`mcp/tools/genes.py`) and capabilities
  prose so "strongest confidence" reads as the `max_confidence_label` field.
- **Explicit count-field contract tests (the real M1 guard):** for `search_panels`
  and `get_panel`, assert across **all four modes** that `n_genes`/`n_regions`/`n_strs`
  are **present** and `number_of_genes`/`number_of_regions`/`number_of_strs` are
  **absent**. *(Rationale: the generic schema-conformance harness alone cannot catch
  M1 — `tool_output_schema` sets `additionalProperties: True` and types `panel`/
  `panels` as bare `_OBJ`/`_OBJ_ARRAY` with no property constraints
  (`schemas.py:17,95,100,102`), so a `number_of_*` payload still validates.)*
- **Schema-conformance harness (secondary net):** for every tool × every
  `response_mode`, validate the structured response against its declared
  `output_schema`. Catches missing top-level keys / wrong envelope shape; the explicit
  count-field tests above carry the M1-specific guarantee. Optionally tighten
  `GET_PANEL_SCHEMA`/`SEARCH_PANELS_SCHEMA` to name the `n_*` count properties.
- **Acceptance:** `search_panels`/`get_panel` expose `n_*` (and never `number_of_*`)
  in all four modes; every tool's output validates against its `output_schema`;
  capabilities no longer reference a non-existent `strongest_confidence` field.

### WS-2 — New tool `compare_panels` (aggregation)

Server-side gene-level diff of 2–5 panels, so an LLM never pulls multiple full gene
lists and diffs them in-context (the canonical token-saving aggregation pattern).

- **Input:** `panels: list[{panel_id:int, region:"uk"|"australia"}]` (length 2–5),
  `min_confidence?: green|amber|red`, `response_mode` (default compact).
- **Output:**
  ```json
  {
    "panels": [{"panel_id":283,"region":"uk","name":"...","n_genes":80}, ...],
    "shared": ["PKD1", "PKD2", ...],
    "only_in": {"283@uk": ["..."], "263@australia": ["..."]},
    "confidence_deltas": [{"gene_symbol":"PKD1","per_panel":{"283@uk":"green","263@australia":"amber"}}],
    "summary": {"n_shared": 42, "n_union": 130}
  }
  ```
- **Mechanics:** per ref, calls **both** existing public service methods —
  `service.get_panel(...)` for metadata (`name`, counts) and
  `service.get_panel_genes(...)` for the gene set — then diffs by `gene_symbol`.
  *(`get_panel_genes` returns `{panel_id, region, entity_type, count, total,
  entities}` with **no panel `name`** (`panelapp_service.py:368-375`), so the metadata
  in the output must come from `get_panel`.)* Both calls share the same
  `_panel_detail` cache, so this is **one upstream fetch per panel**, not two.
  `region='both'` is rejected per-ref with the same tailored message `get_panel` uses.
- **Acceptance:** comparing UK Cystic kidney disease (283) vs AU Renal Cystic Disease
  SuperPanel (263) returns correct shared / only-in sets, panel metadata, and
  confidence deltas; a bad/`both` region per ref returns `invalid_input` with
  `field_errors`; a single cache-warm comparison triggers ≤1 upstream fetch per panel.

### WS-3 — New tool `get_panels_for_genes` (batch membership)

Batch the real workflow "of these N candidate genes, which sit on a panel and at
what confidence?" — collapsing N round-trips into one call.

- **Input:** `gene_symbols: list[str]` (cap **≤ 20**, configurable via settings),
  `region="both"`, `min_confidence?`, `response_mode` (default compact).
- **Output:**
  ```json
  {
    "genes": {"PKD1": {"panel_count":19,"max_confidence_label":"green","panels":[...]}},
    "not_found": ["MADEUPGENE"],
    "truncated": {"requested": 30, "processed": 20, "hint": "cap is 20; resubmit the rest"}
  }
  ```
  (Reuses the existing `max_confidence_label` convention — no new field name, per L2.)
- **Mechanics:** fans out via the existing `service.get_gene_panels(gene_symbol=...)`
  per symbol — reuses cache + the concurrency-capped, jittered client. **N is capped**
  to respect PanelApp's 429 policy; over-cap input is truncated with an explicit
  `truncated` note (no silent drop).
- **Per-symbol error isolation (required):** `get_gene_panels` **raises
  `NotFoundError` for an unknown symbol** (`panelapp_service.py:413`), so a naive
  `asyncio.gather` would fail the whole batch. Each symbol's call is wrapped: a
  `NotFoundError` (or a known gene with zero qualifying panels → `panel_count: 0`)
  routes the symbol into `not_found`; **operational errors** (`DownloadError`,
  `RateLimitError`) **propagate** and fail the envelope as `upstream_unavailable` /
  `rate_limited` (retryable) — partial upstream failure must not masquerade as
  "gene not found".
- **Acceptance:** a mixed list (valid + unknown symbols) returns per-gene membership
  plus a `not_found` list; an injected `DownloadError` on one symbol fails the whole
  call with a retryable envelope (not a silent `not_found`); over-cap input is
  truncated and surfaced; warm-cache reuse confirmed (`_meta.cache: hit` on repeat).

### Response-mode contracts for the new tools (locks WS-2/WS-3 against mode drift)

Both new tools accept `response_mode`; defining the contract now prevents another
M1-class drift. Modes are **strictly additive** (a wider mode only *adds* keys; it
never renames or removes).

**`compare_panels`:**
- `minimal` — `summary{n_shared,n_union}`, `shared` (symbol list), `only_in`
  (symbol lists), `panels:[{panel_id,region}]`. No per-gene confidence.
- `compact` (default) — adds panel metadata (`name`, `n_genes`) and
  `confidence_deltas` (shared genes whose label differs across panels).
- `standard` — adds a full per-panel confidence map for **every** shared gene (not
  just deltas) and `hgnc_id` per gene.
- `full` — adds the underlying `shape_entity(..., "full")` rows per panel.

**`get_panels_for_genes`:** the per-gene `panels` array reuses the existing
`shape_gene_panel_hit` shaping at the requested mode, so it stays identical to
`get_gene_panels` output:
- `minimal` — per gene `{panel_count, max_confidence_label}` + `not_found`; `panels`
  omitted.
- `compact` (default) — adds `panels` as compact gene→panel hit rows.
- `standard` / `full` — `panels` carry the standard/full hit detail.

### WS-4 — Token efficiency: lean `_meta` + trimmed descriptions

- **Lean `_meta` (auto, minimal mode):** in `mcp/envelope.py`, where minimal already
  trims `next_commands` to one entry, also drop `upstream`, `upstream_ms`, and
  `citation_short`. Keep `request_id`, `elapsed_ms`, `cache`, `next_commands[0]`,
  `response_mode`, `unsafe_for_clinical_use`. No new parameter.
- **Trimmed tool descriptions:** shorten each tool's description to ~2 sentences
  (intent + the one key gotcha). The workflow detail already lives in capabilities +
  `panelapp://usage`; remove the duplication from per-tool descriptions (paid on every
  request). Apply to the existing 5 tools and author the 2 new ones lean from the
  start.
- **Acceptance:** a minimal-mode response carries no `upstream`/`upstream_ms` block;
  per-tool description token count drops measurably while capabilities/usage still
  carry full guidance.

### WS-5 — Observability depth: opt-in OTel SDK + OTLP exporter

- Add optional dependency extra `panelapp-link[otel]` =
  `opentelemetry-sdk` + `opentelemetry-exporter-otlp`.
- In `observability/tracing.py`, add a `setup_tracing()` bootstrap that, when
  `PANELAPP_LINK_OTEL__ENABLED=true`, installs a `TracerProvider` with an **OTLP
  exporter only** (endpoint from standard `OTEL_EXPORTER_OTLP_ENDPOINT`). Default off
  → unchanged no-op behaviour.
- **No stdout exporter (stdio-safety).** A console/`ConsoleSpanExporter` writing to
  **stdout would corrupt stdio MCP JSON-RPC framing**. The dev console exporter is a
  *separate* opt-in (`PANELAPP_LINK_OTEL__CONSOLE=true`), is **disabled when the
  active transport is stdio**, and is wired to **stderr only** — never stdout.
- Call the bootstrap from the server lifespan (single-worker).
- **Acceptance:** with the flag on, an in-memory span exporter captures `mcp.tool/*`
  and `panelapp.api/*` spans correlated by `request_id`; with the flag off, zero SDK
  is required and behaviour is identical to today; the console exporter never writes
  to stdout and is suppressed under stdio transport.

### WS-6 — Discoverability, version, and count enrichment

- Register `compare_panels` + `get_panels_for_genes` in: `capabilities.py` `TOOLS`
  tuple (capabilities.py:32), `recommended_workflows` (capabilities.py:82),
  `next_commands.py` (emit `compare_panels` after a multi-hit `search_panels`; emit
  `get_panels_for_genes` is user-driven so no auto-breadcrumb), and `panelapp://usage`.
- **Add a new sibling field `confidence_counts`** (NOT a change to `entity_counts`):
  `{"gene": {"green":N,"amber":N,"red":N}, ...}` in standard+ modes, so "how many
  green genes" needs no entity-list pull. *(`entity_counts` stays exactly as today —
  integer-valued (`shaping.py:101`). Changing its value type to an object would be a
  second breaking change and violate §7 out-of-scope. `confidence_counts` is purely
  additive.)* Computed via a helper to keep the 580-line service body frozen.
- **Bump `server_version` to 0.3.0** at its actual source: `pyproject.toml`
  `version` (the package metadata read by `capabilities._server_version()` →
  `importlib.metadata.version`, `capabilities.py:45`) **and** the
  `panelapp_link/__init__.py` `__version__` fallback (`__init__.py:11`). `config.py`
  needs no edit — it derives the user-agent from `__version__`.
- **Acceptance:** `get_server_capabilities` lists 9 tools and `server_version 0.3.0`;
  `entity_counts` is unchanged (integers) and a new `confidence_counts` field carries
  the breakdown; `next_commands` references the new tools where appropriate.

## 5. Architecture & file impact map

| File | Change | Budget |
|---|---|---|
| `services/shaping.py` (295) | WS-1 M1 remap; WS-6 confidence-count helper | room |
| `services/aggregations.py` **(new)** | WS-2/WS-3 orchestration as free functions taking the service; calls only public service methods | new module, ≤600 |
| `mcp/tools/aggregations.py` **(new)** | WS-2/WS-3 tool registration via `run_mcp_tool` envelope | new module, ≤600 |
| `mcp/tools/__init__.py` (26) | register the new tool module | room |
| `mcp/schemas.py` (150) | output schemas for the 2 new tools; optionally name `n_*` count props on panel schemas | room |
| `mcp/envelope.py` (265) | WS-4 lean `_meta` in minimal mode | room |
| `mcp/tools/genes.py` (122), `mcp/tools/panels.py` (189) | WS-4 trimmed descriptions; WS-1 `resolve_gene` wording | room |
| `mcp/capabilities.py` (243) | WS-6 register tools, workflows, parameter conventions | room |
| `mcp/next_commands.py` (110) | WS-6 `compare_panels` breadcrumb | room |
| `observability/tracing.py` | WS-5 `setup_tracing()` bootstrap | room |
| `config.py`, server lifespan | WS-5 OTel flags (`ENABLED`/`CONSOLE`); WS-3 gene-cap setting | room |
| `pyproject.toml` | WS-5 `[otel]` extra; **version bump → 0.3.0** | n/a |
| `panelapp_link/__init__.py` | version fallback → 0.3.0 (keeps `__version__` in lockstep) | room |
| `panelapp_service.py` (580) | **no change** (must not grow) | frozen |
| `docs/architecture.md`, `usage.md`, capabilities/usage prose | new tools + wording | n/a |
| tests (new/updated) | all workstreams | exempt |

**Key principle:** all new behaviour is additive in new modules; the line-tight
service body is frozen.

## 6. Testing strategy (respx, offline)

- **WS-1:** explicit count-field tests (`n_*` present, `number_of_*` absent) across
  all 4 modes for `search_panels` + `get_panel`; schema-conformance harness (every
  tool × mode validates against `output_schema`); per-tool mode-additivity (wider mode
  is a superset of the narrower); capabilities has no `strongest_confidence` reference.
- **WS-2:** `compare_panels` set-diff correctness (shared / only-in / deltas); panel
  metadata present from `get_panel`; per-ref `region='both'` rejection; 2-panel and
  5-panel cases; ≤1 upstream fetch per panel on warm cache; mode contract honoured.
- **WS-3:** `get_panels_for_genes` mixed valid/unknown → `not_found`; **injected
  `DownloadError` on one symbol fails the whole call (retryable), not a silent
  `not_found`**; over-cap truncation surfaced; warm-cache hit on repeat; `region`
  scoping; mode contract honoured.
- **WS-4:** minimal-mode `_meta` omits `upstream`/`upstream_ms`/`citation_short`,
  retains `request_id`/`elapsed_ms`/`cache`.
- **WS-5:** in-memory span exporter captures correlated spans when enabled; no SDK
  import path required when disabled.
- **WS-6:** capabilities reports 9 tools + `0.3.0`; `entity_counts` unchanged
  (integers) and `confidence_counts` present in standard+; new-tool breadcrumbs
  emitted.
- **WS-5 (stdio-safety):** with `__CONSOLE=true` under stdio transport, no span text
  reaches stdout (assert stdout stays clean / framing intact).

## 7. Out of scope (YAGNI / prior locks)

- Code-mode / dynamic toolsets (overkill at ~9 tools).
- Arbitrary `fields=[...]` projection (the 4 response modes already project).
- Genuine `hgnc_id`→symbol resolution (no new data source; prior lock).
- Redis / shared cache / shared rate limiter / OTel collector topology
  (single-worker decision).
- Any schema-breaking change beyond the M1 `n_*` normalization. In particular
  `entity_counts` keeps its integer values; the per-confidence data ships as the new
  additive `confidence_counts` field.

## 8. Success criteria (the bar for >9/10)

1. **Correctness:** M1 fixed (`n_*` present and `number_of_*` absent in all modes,
   asserted by explicit contract tests — not the permissive schema alone);
   schema-conformance harness green for all tools × modes; L2 wording aligned.
2. **Capability:** `compare_panels` and `get_panels_for_genes` live, read-only,
   upstream-polite (capped fan-out, cache reuse), routed through the envelope so they
   inherit `_meta`/`next_commands`/observability.
3. **Token efficiency:** minimal-mode `_meta` is lean; per-tool descriptions trimmed
   with guidance preserved in capabilities/usage.
4. **Observability:** OTel spans actually export when the flag is on; no-op default
   preserved.
5. **Discoverability:** capabilities lists 9 tools + `0.3.0`; new tools wired into
   workflows + `next_commands`; additive `confidence_counts` field present
   (`entity_counts` unchanged).
6. **Quality gates:** `make ci-local` green; coverage ≥85%; every module ≤600 lines;
   `panelapp_service.py` unchanged.
