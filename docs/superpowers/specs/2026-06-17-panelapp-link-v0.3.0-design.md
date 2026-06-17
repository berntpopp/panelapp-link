# PanelApp-Link — "Beyond 9/10" Phase 2 Spec (v0.3.0)

- **Date:** 2026-06-17
- **Status:** Approved (design)
- **Author:** Senior MCP engineering review (LLM-consumer perspective)
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
- **Schema-conformance test (the durable guard):** for every tool × every
  `response_mode`, validate the structured response against its declared
  `output_schema`. This catches future M1-class regressions automatically.
- **Acceptance:** `search_panels`/`get_panel` shared count-field names are
  mode-invariant; every tool's output validates against its `output_schema` in all
  modes; capabilities no longer reference a non-existent `strongest_confidence` field.

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
- **Mechanics:** calls the existing **public** `service.get_panel_genes(...)` per ref
  (cached, single-flight, concurrency-capped); diffs by `gene_symbol`. `region='both'`
  is rejected per-ref with the same tailored message `get_panel` uses.
- **Acceptance:** comparing UK Cystic kidney disease (283) vs AU Renal Cystic Disease
  SuperPanel (263) returns correct shared / only-in sets and confidence deltas; a
  bad/`both` region per ref returns `invalid_input` with `field_errors`.

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
- **Acceptance:** a mixed list (valid + unknown symbols) returns per-gene membership
  plus a `not_found` list; over-cap input is truncated and surfaced; warm-cache reuse
  confirmed (`_meta.cache: hit` on repeat).

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
  `PANELAPP_LINK_OTEL__ENABLED=true`, installs a `TracerProvider` with an OTLP
  exporter (endpoint from standard `OTEL_EXPORTER_OTLP_ENDPOINT`) — and a console
  exporter for dev. Default off → unchanged no-op behaviour.
- Call the bootstrap from the server lifespan (single-worker).
- **Acceptance:** with the flag on, an in-memory span exporter captures `mcp.tool/*`
  and `panelapp.api/*` spans correlated by `request_id`; with the flag off, zero SDK
  is required and behaviour is identical to today.

### WS-6 — Discoverability, version, and count enrichment

- Register `compare_panels` + `get_panels_for_genes` in: `capabilities.py` `TOOLS`
  tuple (capabilities.py:32), `recommended_workflows` (capabilities.py:82),
  `next_commands.py` (emit `compare_panels` after a multi-hit `search_panels`; emit
  `get_panels_for_genes` is user-driven so no auto-breadcrumb), and `panelapp://usage`.
- **Enrich `get_panel.entity_counts`** with a per-confidence breakdown
  (`{"gene": {"green":N,"amber":N,"red":N}, ...}`) in standard+ modes, so "how many
  green genes" needs no entity-list pull. No new tool; computed in the existing
  shaping/service path (kept out of the 580-line service body via a helper).
- Bump `server_version` to **0.3.0** (single source of truth).
- **Acceptance:** `get_server_capabilities` lists 9 tools and `server_version 0.3.0`;
  `entity_counts` carries confidence breakdowns; `next_commands` references the new
  tools where appropriate.

## 5. Architecture & file impact map

| File | Change | Budget |
|---|---|---|
| `services/shaping.py` (295) | WS-1 M1 remap; WS-6 confidence-count helper | room |
| `services/aggregations.py` **(new)** | WS-2/WS-3 orchestration as free functions taking the service; calls only public service methods | new module, ≤600 |
| `mcp/tools/aggregations.py` **(new)** | WS-2/WS-3 tool registration via `run_mcp_tool` envelope | new module, ≤600 |
| `mcp/tools/__init__.py` (26) | register the new tool module | room |
| `mcp/schemas.py` (150) | output schemas for the 2 new tools; verify panel schema `n_*` | room |
| `mcp/envelope.py` (265) | WS-4 lean `_meta` in minimal mode | room |
| `mcp/tools/genes.py` (122), `mcp/tools/panels.py` (189) | WS-4 trimmed descriptions; WS-1 `resolve_gene` wording | room |
| `mcp/capabilities.py` (243) | WS-6 register tools, workflows, parameter conventions | room |
| `mcp/next_commands.py` (110) | WS-6 `compare_panels` breadcrumb | room |
| `observability/tracing.py` | WS-5 `setup_tracing()` bootstrap | room |
| `config.py`, server lifespan | WS-5 OTel flag; WS-3 gene-cap setting; version 0.3.0 | room |
| `pyproject.toml` | WS-5 `[otel]` extra | n/a |
| `panelapp_service.py` (580) | **no change** (must not grow) | frozen |
| `docs/architecture.md`, `usage.md`, capabilities/usage prose | new tools + wording | n/a |
| tests (new/updated) | all workstreams | exempt |

**Key principle:** all new behaviour is additive in new modules; the line-tight
service body is frozen.

## 6. Testing strategy (respx, offline)

- **WS-1:** mode-invariance test (shared panel count-field names identical across all
  4 modes); schema-conformance harness (every tool × mode validates against
  `output_schema`); capabilities has no `strongest_confidence` reference.
- **WS-2:** `compare_panels` set-diff correctness (shared / only-in / deltas);
  per-ref `region='both'` rejection; 2-panel and 5-panel cases; cache reuse.
- **WS-3:** `get_panels_for_genes` mixed valid/unknown; over-cap truncation surfaced;
  warm-cache hit on repeat; `region` scoping.
- **WS-4:** minimal-mode `_meta` omits `upstream`/`upstream_ms`/`citation_short`,
  retains `request_id`/`elapsed_ms`/`cache`.
- **WS-5:** in-memory span exporter captures correlated spans when enabled; no SDK
  import path required when disabled.
- **WS-6:** capabilities reports 9 tools + `0.3.0`; `entity_counts` confidence
  breakdown present in standard+; new-tool breadcrumbs emitted.

## 7. Out of scope (YAGNI / prior locks)

- Code-mode / dynamic toolsets (overkill at ~9 tools).
- Arbitrary `fields=[...]` projection (the 4 response modes already project).
- Genuine `hgnc_id`→symbol resolution (no new data source; prior lock).
- Redis / shared cache / shared rate limiter / OTel collector topology
  (single-worker decision).
- Any schema-breaking change beyond the M1 `n_*` normalization.

## 8. Success criteria (the bar for >9/10)

1. **Correctness:** M1 fixed (count-field names mode-invariant); schema-conformance
   test green for all tools × modes; L2 wording aligned.
2. **Capability:** `compare_panels` and `get_panels_for_genes` live, read-only,
   upstream-polite (capped fan-out, cache reuse), routed through the envelope so they
   inherit `_meta`/`next_commands`/observability.
3. **Token efficiency:** minimal-mode `_meta` is lean; per-tool descriptions trimmed
   with guidance preserved in capabilities/usage.
4. **Observability:** OTel spans actually export when the flag is on; no-op default
   preserved.
5. **Discoverability:** capabilities lists 9 tools + `0.3.0`; new tools wired into
   workflows + `next_commands`; `entity_counts` carries confidence breakdowns.
6. **Quality gates:** `make ci-local` green; coverage ≥85%; every module ≤600 lines;
   `panelapp_service.py` unchanged.
