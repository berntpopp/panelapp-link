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
           │     per-query live fetch + in-memory _TTLCache.           │
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
           │  tools/           discovery, panels, genes  (7 MCP tools)  │
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
     tools call (never the REST client directly). It owns the in-memory
     `_TTLCache` and a per-region base-URL map, fans `region="both"` out to both
     regions, merges results (deduped by `(region, panel_id)` for panels), filters
     by `min_confidence` rank, and pages with opaque base64 cursors. Each public
     method is `async` and returns a plain JSON-ready payload (the envelope is
     added by the MCP wrapper).
   - `_live_helpers.py` — small, stateless transforms kept out of the service for
     its line budget and independent testing: confidence normalization, panel
     substring matching, entity selection by type, and the gene-identity roll-up.
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
   - `tools/` — the 7 MCP tools grouped by concern (`panels`, `genes`,
     `discovery`).

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
| `get_panel` | `GET /panels/{id}/` | single region; merges signed-off metadata |
| `get_panel_genes` | `GET /panels/{id}/` | selects `genes[]` / `regions[]` / `strs[]`, filters by `min_confidence` |
| `get_gene_panels` | `GET /genes/?entity_name=SYMBOL` (per region) | each result carries its full `panel` object |
| `resolve_gene` | `GET /genes/?entity_name=SYMBOL` (per region) | rolls the gene identity up across regions |
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

The `region` argument is `uk` | `australia` | `both` (default `both`). Search and
gene tools span both regions and tag each result with its region; `get_panel` and
`get_panel_genes` take a single region because a panel id is region-scoped. The
gene tools aggregate a gene's presence across both regions on the fly.

## Error taxonomy

`invalid_input`, `not_found`, `ambiguous_query`, `upstream_unavailable` (API
fetch failure), `rate_limited` (429/403 from PanelApp), `internal_error`. Error
details are masked; the base `_meta` carries the research-use and license
markers, and error envelopes still hand back recovery `next_commands`.
