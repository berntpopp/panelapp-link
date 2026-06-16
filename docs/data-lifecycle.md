# Data freshness & caching

PanelApp-Link is a **pure live-API client**. There is no local database, no
ingest, and no build step: every query calls the public PanelApp REST APIs (UK
and Australia, no auth) at request time and memoizes the raw payloads in a small
**in-memory TTL cache**. Results therefore always reflect the current upstream
data, bounded only by the cache TTL.

```
            ┌──────────────────────── server process ────────────────────────┐
 request ──▶│  MCP tool ──▶ PanelAppService                                    │
            │                  │ cache hit? ──yes──▶ serve from in-memory cache │
            │                  │ cache miss ──────▶ GET PanelApp REST API       │
            │                  ▼                     (UK and/or Australia)       │
            │            in-memory _TTLCache (size-bounded, TTL-expiring)        │
            └─────────────────────────────────────────────────────────────────┘
```

The cache is process-local and best-effort — a latency and politeness
optimization, not a source of truth. Restarting the process empties it; the next
query repopulates it from upstream.

## What is cached

`PanelAppService` memoizes each distinct upstream payload under a stable key, so
repeated and related queries within the TTL window reuse it:

| Cache key | Upstream call | Used by |
|-----------|---------------|---------|
| `panels:{region}` | `GET /panels/` (all pages) | `search_panels` |
| `signedoff:{region}` | `GET /panels/signedoff/` (all pages) | `search_panels`, `get_panel` |
| `panel:{region}:{id}` | `GET /panels/{id}/` | `get_panel`, `get_panel_genes` |
| `genes:{region}:{SYMBOL}` | `GET /genes/?entity_name=SYMBOL` | `get_gene_panels`, `resolve_gene` |

The cache is insertion-ordered and size-bounded: when it reaches
`PANELAPP_LINK_DATA__CACHE_SIZE` entries it evicts the oldest. Each entry expires
after `PANELAPP_LINK_DATA__CACHE_TTL` seconds. Setting either to `0` (size)
disables caching entirely.

## How each tool maps to the API

- **`search_panels`** — fetches the cached full panel list per region
  (`GET /panels/`) and filters it **in memory** (case-insensitive substring over
  name, relevant disorders, disease group, and disease sub-group); PanelApp has
  no usable server-side panel search. Signed-off version/date are merged from the
  cached `GET /panels/signedoff/` listing.
- **`get_panel`** — `GET /panels/{id}/` for a single region, with signed-off
  metadata merged in.
- **`get_panel_genes`** — `GET /panels/{id}/`, then selects `genes[]` /
  `regions[]` / `strs[]` and filters by `min_confidence`.
- **`get_gene_panels`** / **`resolve_gene`** — `GET /genes/?entity_name=SYMBOL`,
  one call per region (PanelApp resolves genes by symbol, not by HGNC id). Each
  result already carries its full `panel` object, so a single call drives both
  the gene-to-panels roll-up and gene resolution.

A panel-first then entity-first workflow on the same panel — or a `resolve_gene`
followed by `get_gene_panels` for the same symbol — reuses cached responses and
issues no further upstream calls within the TTL window.

## Politeness to upstream & rate limits

Because queries are live, the client is deliberately conservative:

- **Bounded concurrency** — a semaphore caps concurrent requests
  (`PANELAPP_LINK_DATA__MAX_CONCURRENCY`, default `4`). PanelApp rate-limits
  aggressive per-IP request bursts.
- **Retries with backoff** — retryable responses (`429`, `500/502/503/504`,
  timeouts, transport errors) are retried with jittered exponential backoff up to
  `PANELAPP_LINK_DATA__MAX_RETRIES` (default `5`).
- **Honours `Retry-After`** — PanelApp sends `Retry-After` with `429`s; the
  client waits the requested delay (capped) before retrying, and gives `429` a
  longer backoff ceiling than ordinary `5xx`.
- **`403` is a hard denial** — never retried; surfaced as a `rate_limited` error.
- **`User-Agent`** — a descriptive `User-Agent`
  (`PANELAPP_LINK_DATA__USER_AGENT`) identifies the client to upstream.

Normal per-query use — one panel, one gene, the occasional panel listing — stays
well under PanelApp's per-IP limits, and the TTL cache further reduces upstream
traffic.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `PANELAPP_LINK_DATA__UK_API_URL` | `…genomicsengland.co.uk/api/v1` | UK PanelApp API base URL |
| `PANELAPP_LINK_DATA__AU_API_URL` | `…panelapp-aus.org/api/v1` | Australia PanelApp API base URL |
| `PANELAPP_LINK_DATA__REQUEST_TIMEOUT` | `60` | Per-request HTTP timeout (seconds) |
| `PANELAPP_LINK_DATA__MAX_CONCURRENCY` | `4` | Max concurrent API requests (kept low; PanelApp throttles bursts) |
| `PANELAPP_LINK_DATA__MAX_RETRIES` | `5` | Retries on 429/5xx/timeout (jittered backoff, honours `Retry-After`) |
| `PANELAPP_LINK_DATA__USER_AGENT` | `PanelApp-Link/<version> …` | User-Agent sent to the PanelApp APIs |
| `PANELAPP_LINK_DATA__CACHE_SIZE` | `512` | Max in-memory cache entries (`0` disables) |
| `PANELAPP_LINK_DATA__CACHE_TTL` | `21600` | In-memory cache TTL in seconds (default 6 hours) |

Tune `CACHE_TTL` to trade freshness against upstream load: a longer TTL serves
more from cache (less load, staler within the window); a shorter TTL refreshes
sooner. The production Compose overlay raises `CACHE_SIZE` and lowers `CACHE_TTL`
for a busier, fresher cache.

## Observability

`get_panelapp_diagnostics` reports the live backend: the upstream source URLs
(UK + Australia), the cache TTL, and current cache stats (`entries`, `maxsize`,
`ttl`). `get_server_capabilities` echoes the same live sources and cache TTL in
its data block. There is no build timestamp or per-region panel count to report —
data is fetched live, so it is always current within the TTL window.
