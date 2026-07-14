# PanelApp-Link Architecture

PanelApp-Link grounds **gene-panel** questions in **PanelApp**, the
crowdsourced, expert-reviewed catalogue of consensus diagnostic gene panels. It
is a **pure live-API client** over **both** PanelApp instances — Genomics England
PanelApp (UK) and PanelApp Australia — and answers panel/gene questions across
either or both regions, querying the public REST APIs per request.

Each panel groups entities — **genes**, **regions** (CNVs), and **STRs** (short
tandem repeats) — and each entity carries a traffic-light **confidence**
(green / amber / red), a mode of inheritance, phenotypes, and supporting
evidence. Panels carry a latest version plus a signed-off version and date.

The server's value over the raw APIs: it **spans both regions** behind one tool
surface, **merges the signed-off version metadata**, **rolls genes up across
regions**, and serves it in a fast, token-efficient, agent-discoverable way that
matches the sibling `*-link` MCP family.

It is a research tool. **Not for clinical decision-making.**

## Live API + in-memory cache (no database)

PanelApp exposes public, no-auth REST APIs. PanelApp-Link calls them directly at
request time and memoizes the raw payloads in a small in-memory **TTL cache**
(default 6h, `PANELAPP_LINK_DATA__CACHE_TTL=21600`):

- there is **no SQLite mirror, no ingest, and no build step** — the server is
  stateless and ready to serve as soon as it starts;
- each query is 1-2 API calls per region; the cache means repeated and related
  queries within the TTL window do not re-hit upstream;
- the cache is process-local and best-effort (insertion-ordered, size-bounded,
  TTL-expiring) — it is a politeness/latency optimization, not a source of truth;
- **single-flight coalescing** (`services/cache.py::RequestCache`) dedupes
  concurrent identical fetches: a burst of identical lookups shares one in-flight
  upstream call instead of stampeding the rate-limited API, so the cold
  double-fetch (`/panels/` + `/panels/signedoff/` for both regions) is paid at
  most once per key per TTL window;
- optional warm-up: `PANELAPP_LINK_DATA__PREWARM=true` pre-fetches the heavy list
  endpoints on start (HTTP/unified) so the first `search_panels` is warm, and
  `PANELAPP_LINK_DATA__REFRESH_INTERVAL=<seconds>` keeps them warm via a
  background task. Both default off, preserving the no-boot-network posture;
- cross-region gene roll-ups and confidence normalization are computed on the fly
  from the live payloads.

Because queries are live, the server is **polite to upstream**: low concurrency
(default 4), jittered exponential backoff, and it honours `Retry-After`. PanelApp
rate-limits aggressive per-IP bursts with HTTP 429; normal per-query use stays
well under the limit.

## Components and data flow

```
        PanelApp UK API                    PanelApp Australia API
   panelapp.genomicsengland.co.uk           panelapp-aus.org/api/v1
        /api/v1 (no auth)                       (no auth)
                 \                              /
                  \                            /
                   v                          v
  api/     ┌──────────────────────────────────────────────────────────┐
           │  client.py  PanelAppRestClient: async httpx, concurrency  │
           │             semaphore, jittered backoff, honours          │
           │             Retry-After, DRF `next` page iteration.       │
           │             base_url passed per call -> serves both       │
           │             regions. list_panels / list_signed_off /      │
           │             get_panel / get_genes_by_entity_name.         │
           └──────────────────────────────────────────────────────────┘
                                  |
                                  v
  services/┌──────────────────────────────────────────────────────────┐
           │  panelapp_service.py  PanelAppService (async):            │
           │     per-query live fetch + single-flight RequestCache.    │
           │     region="both" fans out to ["uk","australia"] & merges.│
           │       search_panels   -> cached /panels/ list, filter in  │
           │                          memory; merge signed-off map     │
           │       get_panel        -> /panels/{id}/                   │
           │       get_panel_genes  -> /panels/{id}/ + entity select   │
           │       get_gene_panels  -> /genes/?entity_name= (per region)│
           │       resolve_gene     -> /genes/?entity_name= (per region)│
           │  _live_helpers.py  pure transforms (confidence, matching, │
           │                    entity select, gene-identity roll-up)  │
           │  shaping.py        response_mode shaping (minimal..full)  │
           └──────────────────────────────────────────────────────────┘
                                  |
                                  v
  mcp/     ┌──────────────────────────────────────────────────────────┐
           │  facade.py        create_panelapp_mcp(): FastMCP + register│
           │  capabilities.py  build_capabilities() + panelapp:// res.  │
           │  envelope.py      run_mcp_tool(), error classification     │
           │  next_commands.py ready-to-call {tool, arguments} chains   │
           │  schemas.py       typed per-tool JSON output schemas       │
           │  tools/        panels, genes, aggregations, discovery (9)  │
           └──────────────────────────────────────────────────────────┘
                                  |
        ┌─────────────────────────┴─────────────────────────┐
        v                                                     v
  FastAPI (/health, /, /docs)                    server_manager.py
        \                                          (transport dispatch)
         └──────────────► unified | http | stdio ◄┘
```

## Layers

1. **API client** (`panelapp_link/api/`)
   - `client.py` — `PanelAppRestClient`: async httpx with a concurrency
     semaphore, jittered exponential backoff on `{429, 500, 502, 503, 504}` that
     honours `Retry-After`, a custom `User-Agent`, an injectable client for
     tests, and DRF `next`-page iteration. The base URL is supplied **per call**,
     so one client serves both regions. Methods: `list_panels`,
     `list_signed_off`, `get_panel`, `get_genes_by_entity_name`. A `403` is a
     hard denial (never retried); `429` is retried with a longer ceiling.

2. **Service** (`panelapp_link/services/`)
   - `panelapp_service.py` — `PanelAppService`, the async business logic that all
     tools call (never the REST client directly). It owns the single-flight
     `RequestCache` (`services/cache.py`) and a per-region base-URL map, fans
     `region="both"` out to both
     regions, merges results (deduped by `(region, panel_id)` for panels), filters
     by `min_confidence` rank, and pages with opaque base64 cursors. Each public
     method is `async` and returns a plain JSON-ready payload (the envelope is
     added by the MCP wrapper).
   - `_live_helpers.py` — small, stateless transforms kept out of the service for
     its line budget and independent testing: confidence normalization, panel
     word-prefix matching + field-weighted scoring, entity selection by type, and
     the gene-identity roll-up.
   - `shaping.py` — `response_mode` shaping across minimal / compact / standard /
     full, plus the `normalize_*` / `shape_*` row helpers.

3. **MCP layer** (`panelapp_link/mcp/`)
   - `facade.py` — builds the FastMCP server and registers all tools, resources,
     and annotations.
   - `capabilities.py` — `build_capabilities()` and the `panelapp://` resource
     family (`capabilities`, `usage`, `reference`, `license`, `citation`,
     `research-use`).
   - `envelope.py` — wraps every tool in a typed response envelope with error
     classification and a base `_meta` block.
   - `next_commands.py` — builds the `_meta.next_commands` ready-to-call chains.
   - `schemas.py` — typed per-tool JSON output schemas.
   - `tools/` — the 9 MCP tools grouped by concern (`panels`, `genes`,
     `aggregations`, `discovery`).
   - `services/aggregations.py` — free functions (`compare_panels`,
     `panels_for_genes`) that compose the **public** service methods into
     higher-order, token-saving views; the line-tight `panelapp_service.py` stays
     frozen and all fan-out rides its cache + concurrency-capped client.

4. **Server** (`panelapp_link/server_manager.py`, `server.py`, `mcp_server.py`)
   - `server_manager.py` — `UnifiedServerManager`, the single entry point for the
     three transports.
   - `server.py` — the `panelapp-link` console script (argparse transport switch).
   - `mcp_server.py` — the `panelapp-link-mcp` stdio entry.

5. **Configuration** (`panelapp_link/config.py`)
   - Pydantic-settings with the `PANELAPP_LINK_` env prefix and nested `data`
     config (`PANELAPP_LINK_DATA__*`, double-underscore delimiter). The data
     config describes only the upstream APIs, the HTTP client, and the in-memory
     cache — there is no database or data directory.

## How each tool maps to the API

| Tool | API calls | Notes |
|------|-----------|-------|
| `search_panels` | cached `GET /panels/` (per region) | filtered in memory; signed-off merged from cached `GET /panels/signedoff/` |
| `get_panel` | `GET /panels/{id}/` | single region; merges signed-off metadata; adds `confidence_counts` (per-type traffic-light tallies) in `standard`/`full` |
| `get_panel_genes` | `GET /panels/{id}/` | selects `genes[]` / `regions[]` / `strs[]`, filters by `min_confidence` |
| `get_gene_panels` | `GET /genes/?entity_name=SYMBOL` (per region) | each result carries its full `panel` object |
| `resolve_gene` | `GET /genes/?entity_name=SYMBOL` (per region) | rolls the gene identity up across regions |
| `compare_panels` | cached `GET /panels/{id}/` (one per ref) | diffs 2–5 panels' genes (`shared` / `only_in` / `confidence_deltas`); concrete regions only |
| `get_panels_for_genes` | `GET /genes/?entity_name=SYMBOL` (per gene, per region) | batch membership ≤ 20 genes; per-symbol `not_found` isolation; semaphore-capped fan-out |
| `get_server_capabilities` | none | static capabilities + live source/cache info |
| `get_panelapp_diagnostics` | none | live sources, cache TTL, and cache stats |

All raw payloads are memoized in the TTL cache, so a panel-first then
entity-first workflow on the same panel/gene reuses cached responses.

## Transports

`server.py` (or the `panelapp-link` console script) dispatches three transports
via `UnifiedServerManager`:

- **`unified`** (default) — FastAPI REST on `/` and MCP streamable HTTP on `/mcp`
  over a single port (`8000`).
- **`http`** — FastAPI REST only.
- **`stdio`** — FastMCP over stdio, for Claude Desktop and similar local clients
  (also exposed as the `panelapp-link-mcp` console script via `mcp_server.py`).

## Confidence model

Each entity's `confidence_level` arrives from PanelApp as an int or string and is
normalized on the fly into a stable label and rank used for filtering and
ordering:

| `confidence_level` | `confidence_label` | `confidence_rank` |
|--------------------|--------------------|-------------------|
| `"3"`, `"4"` | green | 3 |
| `"2"` | amber | 2 |
| `"1"`, `"0"` | red | 1 |

`min_confidence` filters by rank: `green` returns only green entities, `amber`
returns amber + green, and `red` returns all. Green is the conventional
"diagnostic-grade" tier.

## Signed-off versions

PanelApp keeps a panel's latest (possibly in-progress) version plus a separately
**signed-off** version. PanelApp-Link reports the latest `version` /
`version_created` on the panel and merges `signed_off_version` /
`signed_off_date` from the cached `/panels/signedoff/` listing (joined by panel
`id`), so consumers can tell the current head from the last expert-signed release.

## Regions

The `region` argument is `uk` | `australia` | `both`. It defaults to `both` on the
search and gene tools (`search_panels`, `get_gene_panels`, `get_panels_for_genes`,
`resolve_gene`), which span both regions and tag each result with its region.
`get_panel` and `get_panel_genes` **require** a single concrete region (`uk` or
`australia`) — it has no default, and `both` is rejected with `invalid_input` —
because a panel id is region-scoped. `compare_panels` has no top-level `region`:
each `panels[]` ref carries its own. The gene tools aggregate a gene's presence
across both regions on the fly.

## Error taxonomy

`invalid_input`, `not_found`, `upstream_unavailable` (API fetch failure),
`rate_limited` (429/403 from PanelApp), `limit_exceeded` (untrusted-text size /
count cap), `internal_error`. This is the same list `get_server_capabilities`
advertises as `error_codes_list`, and it is exactly what `envelope._classify`
emits — no other code is ever returned. Error
details are masked; the base `_meta` carries the research-use and license
markers, and error envelopes still hand back recovery `next_commands`.

## Observability

Three cooperating layers (`panelapp_link/observability/`), so you can see the
*system*, not just one call:

- **Per-call breadcrumbs** — every envelope `_meta` carries a 12-hex `request_id`,
  `elapsed_ms`, a `cache` label (`hit` | `miss` | `coalesced` | `partial`), and
  per-region upstream timing (`upstream_ms` + `upstream{region:{calls,ms}}`), so
  an agent or operator can see *why* a call took N ms. Scoped via a
  `ContextVar` (`telemetry.py`) the cache layer writes and the envelope reads.
- **RED metrics** (`metrics.py`) — process-wide request rate, errors by code,
  tool + per-region upstream duration p50/p95/p99, and cache hit ratio. Exported
  as Prometheus text at `GET /metrics` (hand-rendered, no scrape-side dependency)
  and folded into `get_panelapp_diagnostics`.
- **Tracing** (`tracing.py`) — OpenTelemetry spans wrap each tool call
  (`mcp.tool/<name>`) and each upstream region fetch (`panelapp.api/<endpoint>`),
  the latter a child of the former, so one MCP call is one trace correlated by
  `request_id`. Instrumented with the OTel **API** (a no-op until an operator
  configures an SDK + exporter — the standard library-instrumentation pattern).
  `setup_tracing()` is the opt-in bootstrap: with the `otel` extra installed and
  `PANELAPP_LINK_OTEL__ENABLED=true`, it installs an OTLP `TracerProvider` on
  startup (both transports). It degrades to a no-op when the extra is absent, and
  the optional console exporter is stderr-only and suppressed under stdio so it
  can never corrupt the JSON-RPC channel.

The envelope (`run_mcp_tool`) is the single choke point that opens the telemetry
scope + trace span, records RED metrics, and folds the cache/upstream block into
`_meta` for every tool — success or error. `minimal` mode sheds the heaviest
breadcrumbs (the `upstream` timing and the short citation) and keeps a single
`next_commands` step, for sweep / agent-loop workloads.

## Politeness & rate limiting

The server is read-only over public data and ships without auth. Beyond the
upstream-facing politeness (semaphore, jittered backoff, `Retry-After`,
single-flight), an **opt-in** per-process token bucket
(`PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE`, `mcp/rate_limit.py`) caps the MCP
tool-call rate so one unauthenticated client cannot induce heavy UK+AU fan-out
and trigger 429s for everyone. Over the cap a call returns a structured
`rate_limited` envelope and never reaches upstream. Disabled by default.
