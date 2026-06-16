# PanelApp-Link Architecture

PanelApp-Link grounds **gene-panel** questions in **PanelApp**, the
crowdsourced, expert-reviewed catalogue of consensus diagnostic gene panels. It
mirrors **both** PanelApp instances — Genomics England PanelApp (UK) and PanelApp
Australia — into a local read-only SQLite database and answers panel/gene
questions across either or both regions.

Each panel groups entities — **genes**, **regions** (CNVs), and **STRs** (short
tandem repeats) — and each entity carries a traffic-light **confidence**
(green / amber / red), a mode of inheritance, phenotypes, and supporting
evidence. Panels carry a latest version plus a signed-off version and date.

The server's value over the raw APIs: it **mirrors both regions** into one local
store, **merges the signed-off version metadata**, **rolls genes up across
regions**, and serves it in a fast, token-efficient, agent-discoverable way that
matches the sibling `*-link` MCP family.

It is a research tool. **Not for clinical decision-making.**

## Why SQLite (mirror, not live calls)

PanelApp exposes public, no-auth REST APIs, but the data is large-ish (UK ~434
panels, AU ~261), slow-changing, and spread across many `/panels/{id}/` detail
requests. So instead of calling the APIs at request time, PanelApp-Link crawls
both regions once into a local **SQLite + FTS5** artifact and queries it
in-process:

- queries are local, deterministic, and sub-millisecond — no network at query
  time, no upstream rate limits, no flaky external dependency;
- the crawl is an explicit, idempotent ETL step that can be refreshed
  incrementally;
- cross-region gene roll-ups and confidence normalization are precomputed at
  build time, so tools just read derived rows.

The `api/` REST client is used **only** by ingest; the MCP tools never touch the
live APIs.

## Components and data flow

```
        PanelApp UK API                    PanelApp Australia API
   panelapp.genomicsengland.co.uk           panelapp-aus.org/api/v1
        /api/v1 (no auth)                       (no auth)
                 \                              /
                  \                            /
                   v                          v
  ingest/  ┌──────────────────────────────────────────────────────────┐
           │  client.py     async httpx, semaphore, jittered backoff,   │
           │  (api/)        DRF page iteration over both regions        │
           │  downloader.py crawl: list /panels -> fetch /signedoff map │
           │                -> fetch each /panels/{id}/ (bounded conc.) │
           │  builder.py    build panelapp.sqlite.tmp, then atomic swap │
           │  cli.py        panelapp-link-data build | refresh | status │
           └──────────────────────────────────────────────────────────┘
                                  |
                                  v
  data/    ┌──────────────────────────────────────────────────────────┐
           │  SQLite + FTS5 store  (data/panelapp.sqlite)              │
           │   panel      one row per (region, panel_id): latest       │
           │              version + signed_off_version / signed_off_date│
           │   entity     one row per (region, panel_id, type, name):  │
           │              gene | region | str; confidence label + rank │
           │   gene       derived roll-up across regions: panel_count, │
           │              regions, max confidence                      │
           │   panel_fts  FTS5 over name / disorders / disease group   │
           │   meta       single-row build provenance                  │
           │  repository.py  PanelAppRepository: read-only SQLite (ro) │
           └──────────────────────────────────────────────────────────┘
                                  |
                                  v
  services/┌──────────────────────────────────────────────────────────┐
           │  panelapp_service.py  search, panels, genes, roll-ups     │
           │  shaping.py           response_mode shaping (minimal..full)│
           │  refresh.py           in-app conditional-refresh scheduler │
           └──────────────────────────────────────────────────────────┘
                                  |
                                  v
  mcp/     ┌──────────────────────────────────────────────────────────┐
           │  facade.py        create_panelapp_mcp(): FastMCP + register│
           │  capabilities.py  build_capabilities() + panelapp:// res.  │
           │  envelope.py      run_mcp_tool(), error classification     │
           │  next_commands.py ready-to-call {tool, arguments} chains   │
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
     semaphore, jittered exponential backoff on `{429, 500, 502, 503, 504}`, a
     custom `User-Agent`, an injectable client for tests, and async generators
     over DRF pages (`list_panels`, `list_signed_off`, `get_panel`). Used only
     by ingest.

2. **Ingest** (`panelapp_link/ingest/`)
   - `downloader.py` — crawls both regions: list `/panels/`, fetch the
     `/panels/signedoff/` map, then fetch each `/panels/{id}/` under bounded
     concurrency. Returns in-memory structures.
   - `builder.py` — atomic build: write to `panelapp.sqlite.tmp`, run the schema,
     insert panels (with signed-off merged) and entities (with confidence label /
     rank normalized), roll genes up across regions, build the FTS index, write
     `meta`, then atomic rename into place.
   - `lock.py` — a cross-process build lock so concurrent builders never crawl or
     rebuild at the same time.
   - `cli.py` — the `panelapp-link-data` console script
     (`build` / `refresh` / `status`).

3. **Data store** (`panelapp_link/data/`)
   - `schema.sql` — DDL for `panel`, `entity`, the derived `gene` roll-up, the
     `panel_fts` FTS5 index, and the single-row `meta` table (`schema_version`,
     source URLs, per-region panel counts, entity/gene counts, build timestamp,
     and a `panel_versions_json` map for incremental refresh).
   - `repository.py` — `PanelAppRepository`, a read-only SQLite query layer opened
     with `mode=ro`.

4. **Services** (`panelapp_link/services/`)
   - `panelapp_service.py` — business logic: panel search, panel detail, panel
     entities, gene-to-panels, gene resolution, plus a small TTL cache.
   - `shaping.py` — `response_mode` shaping across minimal / compact / standard /
     full.
   - `refresh.py` — the in-app conditional-refresh scheduler (unified/http only).

5. **MCP layer** (`panelapp_link/mcp/`)
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

6. **Server** (`panelapp_link/server_manager.py`, `server.py`, `mcp_server.py`)
   - `server_manager.py` — `UnifiedServerManager`, the single entry point for the
     three transports.
   - `server.py` — the `panelapp-link` console script (argparse transport switch).
   - `mcp_server.py` — the `panelapp-link-mcp` stdio entry.

7. **Configuration** (`panelapp_link/config.py`)
   - Pydantic-settings with the `PANELAPP_LINK_` env prefix and nested `data`
     config (`PANELAPP_LINK_DATA__*`, double-underscore delimiter).

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
normalized at ingest time into a stable label and rank used for filtering and
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
**signed-off** version. PanelApp-Link records the latest `version` /
`version_created` on the panel row and merges `signed_off_version` /
`signed_off_date` from `/panels/signedoff/` (joined by panel `id`), so consumers
can tell the current head from the last expert-signed release.

## Regions

The `region` argument is `uk` | `australia` | `both` (default `both`). Search and
gene tools span both regions and tag each result with its region; `get_panel`
takes a single region because a panel id is region-scoped. The derived `gene`
table aggregates a gene's presence across both regions.

## Bootstrap and refresh

- The ETL is explicit: `make data` runs `panelapp-link-data build` (forced full
  crawl + rebuild). `panelapp-link-data refresh` re-lists panels, compares them to
  the stored `panel_versions_json`, and re-fetches only changed/new panels.
  `panelapp-link-data status` prints provenance.
- On server startup, if the database is missing and
  `PANELAPP_LINK_DATA__AUTO_BOOTSTRAP=true` (default), the server builds it on
  first use by crawling both regions. Otherwise data-dependent tools return a
  typed `data_unavailable` envelope telling the agent to run `make data`.
- The `meta` row records the source URLs, per-region panel counts, entity / gene
  counts, schema version, build timestamp, and per-panel versions.
  `get_panelapp_diagnostics` reports this freshness.

## Error taxonomy

`invalid_input`, `not_found`, `ambiguous_query`, `data_unavailable`,
`upstream_unavailable` (crawl failure), `rate_limited` (429/403 from PanelApp),
`internal_error`. Error details are masked; the base `_meta` carries the
research-use and license markers.
