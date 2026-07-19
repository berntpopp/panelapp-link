# PanelApp-Link — "Beyond 9/10" Improvement Spec

- **Date:** 2026-06-17
- **Status:** Approved (design)

> Historical record — this design records the proposed system as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Author:** Senior MCP engineering review
- **Scope decision:** Ship + fix (full) — commit the in-flight observability/cache/
  rate-limit work, fix the remaining contract/quality/latency gaps, flip `prewarm`
  on, then rebuild & redeploy.

## 1. Background

An MCP black-box evaluation scored the *deployed* `panelapp-link` server at ~8–9/10
(discoverability and token efficiency strong; one High-severity contract bug; slow
`search_panels`; thin observability on the live build).

A source-level audit then established a key fact: **the deployed server is a stale
build.** The working tree (uncommitted, written 2026-06-17) already contains most of
the recommended infrastructure. The improvement work is therefore *partly done and
unshipped*, plus a short list of genuinely open gaps.

### Already built in the working tree (uncommitted)

| Capability | Location | State |
|---|---|---|
| Single-flight request coalescing | `panelapp_link/services/cache.py` (`RequestCache.get_or_fetch`) | Done |
| OpenTelemetry tracing (API-only, no-op until SDK wired) | `panelapp_link/observability/tracing.py` | Done |
| RED metrics + `/metrics` + cache hit/miss/coalesced | `panelapp_link/observability/metrics.py`, `server_manager.py` | Done |
| Per-request cache/upstream telemetry folded into `_meta` | `panelapp_link/observability/telemetry.py`, `mcp/envelope.py` | Done |
| MCP-layer rate limiting (opt-in token bucket) | `panelapp_link/mcp/rate_limit.py` | Done |
| Prewarm + stale-while-revalidate background refresh | `services/panelapp_service.py`, `config.py` | Built; library default off (offline-safe), **enabled in `docker-compose.prod.yml`** (`PREWARM=true`, `REFRESH_INTERVAL=3600`) |
| Deployment env: prewarm + refresh + rate-limit cap | `docker/docker-compose.prod.yml` | Done (uncommitted) |
| Tests for all of the above | `tests/test_cache.py`, `test_metrics.py`, `test_telemetry.py`, `test_tracing.py`, `test_rate_limit.py`, `test_envelope_observability.py`, `test_service_observability.py` | Done |

### Genuinely open gaps (verified in current code)

1. **🔴 B-1 — `hgnc_id`-only contract break (High).** `get_gene_panels` requires
   `gene_symbol` and rejects `hgnc_id`-only (`panelapp_service.py:399–406`; `hgnc_id`
   used only as a post-filter at `:417`), yet `next_commands.py:52–53`
   (`after_resolve_gene`) still emits the breadcrumb `get_gene_panels(hgnc_id=…)`.
   An agent that follows `resolve_gene`'s own `next_commands[0]` verbatim hits
   `invalid_input`. The tool description (`mcp/tools/genes.py`) and capabilities
   `parameter_conventions` also advertise `hgnc_id` as a usable identifier. Commit
   `61fc0dc` only cleaned up `resolve_gene`'s signature; it did not fix this.
2. **🟡 B-2 — substring over-matching (Low/quality).** `_live_helpers.py:29–37`
   (`panel_matches`) is a raw case-folded substring test, so `"renal"` matches
   `"Adrenal insufficiency"`. No tokenization, no ranking.
3. **🟡 B-3 — slow-path recovery nudge (Low).** `next_commands.py:100–101`: on
   `get_panel` / `get_panel_genes` `not_found`, the recovery breadcrumb is
   `search_panels(query="")`, which triggers the heavy full-list pull.
4. **⚠️ Deploy gap (the real latency fix).** The library default is `prewarm=False`
   / `refresh_interval=0` (offline-safe), but the prod compose **already enables**
   prewarm + refresh — so the only thing standing between users and a warm,
   sub-second `search_panels` is that **none of the in-flight work is committed or
   deployed**. Flipping the library config default is explicitly *not* wanted: it
   would break the offline test suite (the app lifespan prewarms inside
   `TestClient`) and contradict the documented stateless no-boot-network posture.

## 2. Decisions (locked)

- **B-1 fix = align the contract** (not genuine `hgnc_id` resolution). `gene_symbol`
  is the canonical query key; `hgnc_id` is an optional disambiguation filter. No new
  data source — preserves the "pure live-API client, no local DB" principle.
- **Prewarm + background refresh enabled at the deployment layer** (env in
  `docker/docker-compose.prod.yml`), *not* the library config default. Keeps the
  stateless no-boot-network posture (and the offline test suite) intact while the
  shipped container is warm from boot. Already present in the in-flight prod compose
  — so WS-E is verification + a regression test, not a default flip.
- **`server_version` 0.2.0** to mark the new observability surface
  (`capabilities_version` is a content hash and rehashes automatically).
- **B-2 = per-token, word-boundary prefix matching** with light field ranking.

## 3. Constraints

- **File-size budget: 600 lines/module**, enforced by `make lint-loc` (in
  `ci-local`). `panelapp_service.py` is at **577/600** — new logic goes into
  `_live_helpers.py` (90 ln) and `next_commands.py` (108 ln), never the service body.
- Public MCP tools stay **read-only, research-use scoped**; no new tools.
- Tests must not hit the network except under the `integration` marker; use `respx`
  with committed fixtures (`tests/fixtures/`, pattern in `tests/conftest.py`
  `build_router`).
- `make ci-local` must pass: format, lint, lint-loc, mypy (strict, 3.12), tests,
  coverage gate **≥85%**.

## 4. Workstreams

### WS-A — Ship the in-flight work (deploy sync)

The single biggest lever on the *live* rating: the infrastructure exists but isn't
deployed.

- Verify wiring end-to-end:
  - `mcp/envelope.py` folds `telemetry.telemetry_meta(scope)` into every `_meta`.
  - `server_manager.py` exposes `GET /metrics` (Prometheus 0.0.4 text).
  - `mcp/facade.py` registers the rate-limit middleware.
  - `services/panelapp_service.py` reads/writes through `RequestCache`.
- `make ci-local` green, then commit as cohesive commits (observability; cache;
  rate-limit; config/version).
- Rebuild the Docker image and redeploy.
- **Acceptance:** a live tool response carries `_meta.cache` and `_meta.upstream_ms`;
  `GET /metrics` returns Prometheus text; `get_server_capabilities` reports
  `server_version: 0.2.0`.

### WS-B — B-1: align the `hgnc_id` contract (High)

- `mcp/next_commands.py:after_resolve_gene` → emit
  `get_gene_panels(gene_symbol=<symbol>)` whenever a symbol is present (it always is
  in the resolved gene object). Removes the broken hop. (Keep a `gene_symbol`
  fallback path only.)
- `mcp/tools/genes.py` — reword `get_gene_panels` tool + param descriptions:
  `gene_symbol` is the query key (required); `hgnc_id` is an **optional
  disambiguation filter** applied to results. Drop "EITHER `gene_symbol` OR
  `hgnc_id`".
- `mcp/capabilities.py` — `parameter_conventions.hgnc_id`: change "mutually exclusive
  with gene_symbol" → "optional result filter; `gene_symbol` drives the query".
- Service validation unchanged — now behaviour, description, capabilities, and the
  breadcrumb all agree.
- **Acceptance:** following `resolve_gene`'s `next_commands[0]` verbatim returns
  `success: true`. Contract is internally self-consistent.

### WS-C — B-2: word-aware, ranked search (quality)

- `_live_helpers.py`:
  - Replace `panel_matches` substring logic with **per-token prefix matching on word
    boundaries**: tokenize both query and haystacks; a query token matches a haystack
    token when the haystack token *starts with* it; a multi-token query requires all
    tokens to match (AND). Result: `"renal"` ✗ `"adrenal"`, `"cyst"` ✓ `"Cystic…"`.
  - Add `rank_panels(panels, needle) -> list[panel]` returning matches ordered by
    field weight: **name > relevant_disorders > disease_group/disease_sub_group**.
- `services/panelapp_service.search_panels`: swap the bool filter for
  `rank_panels(...)` (≈1-line change; ranking lives in the helper to protect the
  service line budget).
- **Acceptance:** `search_panels("renal")` excludes "Adrenal insufficiency";
  name-field matches sort ahead of disorder-only matches.

### WS-D — B-3: stop nudging to the slow path (minor)

- `mcp/next_commands.py:recovery_commands` — for `get_panel` / `get_panel_genes`
  `not_found`, return `[]` (a bad `panel_id` has no name to search; an empty query
  triggers the 13–17 s full-list pull). The prose `recovery_action: "switch_tool"`
  already conveys intent. Other tools' recovery branches are unchanged.
- **Acceptance:** a `get_panel` `not_found` envelope emits no
  `search_panels(query="")`.

### WS-E — prewarm in the deployment (latency)

- **No library default change.** `config.py` keeps `prewarm=False` /
  `refresh_interval=0` so library consumers and the offline test suite never touch
  the network at boot.
- **Verify** `docker/docker-compose.prod.yml` enables `PANELAPP_LINK_DATA__PREWARM=true`
  and `PANELAPP_LINK_DATA__REFRESH_INTERVAL=3600` (already present in the in-flight
  tree). Prewarm is non-fatal on failure (logs + continues) and runs through the
  concurrency-capped, jittered client.
- **Add a regression test** that the lifespan prewarm path populates the cache when
  enabled, using respx (no real network).
- **Acceptance:** after a prod-config boot, the first `search_panels` is a cache hit
  (sub-second).

### Cross-cutting

- Update `docs/architecture.md` and capabilities/usage prose where the `hgnc_id`
  contract wording changes.
- Bump `server_version` to `0.2.0` (single source of truth).

## 5. Testing strategy

respx-mocked unit tests following `tests/conftest.py` `build_router`:

- **B-1 regression (the headline test):** call `resolve_gene`, then follow
  `_meta.next_commands[0]` verbatim into `get_gene_panels`; assert `success: true`.
  Assert `after_resolve_gene` emits `gene_symbol` (not `hgnc_id`). Assert the reworded
  description/capabilities no longer claim `hgnc_id` drives the query.
- **B-2:** `panel_matches`/`rank_panels` unit tests — `"renal"` excludes
  "Adrenal insufficiency"; `"cyst"` matches "Cystic kidney disease"; multi-token AND;
  name matches rank above disorder-only matches.
- **B-3:** `recovery_commands` for `get_panel`/`get_panel_genes` `not_found` returns
  `[]`; no `search_panels(query="")`.
- **WS-E:** lifespan runs `prewarm` under the default config; `service.prewarm()`
  populates the cache so a subsequent `search_panels` is a hit.
- **WS-A post-deploy smoke (manual/integration):** `_meta` telemetry block present;
  `/metrics` responds; capabilities reports `0.2.0`.

## 6. Out of scope

- Wiring a concrete OpenTelemetry **SDK + exporter** (the API-only no-op instrumentation
  ships as-is; operators opt in by installing the SDK).
- Multi-replica shared cache / shared rate limiter (single-worker deployment; documented
  as a future step in `rate_limit.py`).
- Genuine `hgnc_id`→symbol resolution (rejected in favour of contract alignment).
- Any new MCP tools or schema-breaking changes.

## 7. Success criteria (the bar for >9/10)

1. **B-1:** `resolve_gene` → its own breadcrumb → `get_gene_panels` succeeds; contract
   self-consistent across description, capabilities, and behaviour.
2. **B-2:** search precision fixed (`"renal"` ↛ "Adrenal…") and results ranked.
3. **B-3:** no slow-path recovery nudge on panel `not_found`.
4. **Latency:** `search_panels` is fast from the first call post-deploy (prewarmed).
5. **Observability live:** `_meta` carries cache/upstream telemetry; `/metrics`
   served; OTel spans available when an SDK is wired.
6. **Quality gates:** `make ci-local` green; coverage ≥85%; every module ≤600 lines;
   `server_version` 0.2.0.

## 8. Affected files (impact map)

| File | Change | Budget risk |
|---|---|---|
| `mcp/next_commands.py` (108) | B-1 breadcrumb, B-3 recovery | none |
| `mcp/tools/genes.py` | B-1 description wording | none |
| `mcp/capabilities.py` | B-1 `parameter_conventions` wording | none |
| `services/_live_helpers.py` (90) | B-2 matching + `rank_panels` | none |
| `services/panelapp_service.py` (577) | B-2 call-site swap (≈1 line) | **tight — keep additions out** |
| `docker/docker-compose.prod.yml` | WS-E: verify prewarm/refresh/rate-limit env (already present) | none |
| `__init__`/version source | bump 0.2.0 | none |
| `docs/architecture.md`, capabilities/usage prose | contract wording | none |
| new/updated tests | all workstreams | tests exempt from budget |
| `observability/`, `services/cache.py`, `mcp/rate_limit.py` + tests | WS-A: commit as-is | already within budget |
