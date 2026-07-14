# Configuration

Settings load from environment variables prefixed `PANELAPP_LINK_` and, if
present, an optional `.env` file in the working directory. Nested config uses a
**double underscore**: the data block is addressed as
`PANELAPP_LINK_DATA__CACHE_TTL`, the tracing block as
`PANELAPP_LINK_OTEL__ENABLED`.

Copy [`.env.example`](../.env.example) to `.env` and adjust. Every variable has a
working default — PanelApp-Link starts with no configuration at all, because both
upstream APIs are public and **no API key or token is required**.

## Server & transport

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_HOST` | `127.0.0.1` | Bind address |
| `PANELAPP_LINK_PORT` | `8000` | Bind port (1024-65535) |
| `PANELAPP_LINK_TRANSPORT` | `unified` | `unified` (REST + MCP) \| `http` \| `stdio` |
| `PANELAPP_LINK_MCP_PATH` | `/mcp` | MCP endpoint path (a leading `/` is added if missing) |
| `PANELAPP_LINK_RELOAD` | `false` | Auto-reload (development only) |

Three transports are served from one codebase: `unified` (FastAPI `/health`, `/`,
`/docs`, `/metrics` **plus** MCP Streamable HTTP at `MCP_PATH`), `http`, and
`stdio` (the local Claude Desktop path, via the `panelapp-link-mcp` entry point).

## HTTP boundary: Host, Origin, CORS

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | Exact HTTP `Host` allowlist, JSON list |
| `PANELAPP_LINK_ALLOWED_ORIGINS` | `[]` | Exact browser `Origin` allowlist, JSON list |
| `PANELAPP_LINK_CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | Origins echoed in CORS response headers |
| `PANELAPP_LINK_CORS_ALLOW_CREDENTIALS` | `false` | Off by design: this backend is unauthenticated and holds no cookie or session, so credentialed CORS is meaningless. `true` together with a `*` origin is rejected at startup |
| `PANELAPP_LINK_CORS_ALLOW_METHODS` | `["GET","POST","OPTIONS"]` | CORS methods |
| `PANELAPP_LINK_CORS_ALLOW_HEADERS` | `["*"]` | CORS request headers |

Host and Origin validation is **strict on every HTTP route**, and the two
allowlists **reject wildcards** (`*`, `?`, `[`, `]`) at startup — list exact
values only.

Two rules that are easy to get wrong:

- **Behind a reverse proxy**, add the public hostname as an exact entry in
  `PANELAPP_LINK_ALLOWED_HOSTS` alongside the loopback defaults, or every proxied
  request is rejected.
- **Request-Origin validation and browser CORS are independent controls.** An
  absent `Origin` header (the normal case for non-browser MCP clients) stays
  allowed. A browser deployment must configure the same exact origins in **both**
  `PANELAPP_LINK_ALLOWED_ORIGINS` and `PANELAPP_LINK_CORS_ORIGINS`.

## PanelApp live API & in-memory cache

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_DATA__UK_API_URL` | `https://panelapp.genomicsengland.co.uk/api/v1` | Genomics England PanelApp (UK) API base |
| `PANELAPP_LINK_DATA__AU_API_URL` | `https://panelapp-aus.org/api/v1` | PanelApp Australia API base |
| `PANELAPP_LINK_DATA__REQUEST_TIMEOUT` | `60` | Per-request HTTP timeout, seconds (5-900) |
| `PANELAPP_LINK_DATA__MAX_CONCURRENCY` | `4` | Max concurrent upstream requests. Kept low deliberately: PanelApp rate-limits aggressive per-IP bursts with HTTP 429 |
| `PANELAPP_LINK_DATA__MAX_RETRIES` | `5` | Retries on 429/5xx/timeout, jittered backoff, honours `Retry-After` |
| `PANELAPP_LINK_DATA__USER_AGENT` | `PanelApp-Link/<version> (+repo URL)` | User-Agent sent to the PanelApp APIs |
| `PANELAPP_LINK_DATA__CACHE_SIZE` | `512` | Max in-memory cache entries; `0` disables caching |
| `PANELAPP_LINK_DATA__CACHE_TTL` | `21600` | Cache TTL in seconds (6 hours; also baked into the Docker image) |
| `PANELAPP_LINK_DATA__PREWARM` | `false` | On start (HTTP/unified), pre-fetch the heavy panel + signed-off lists for both regions so the first `search_panels` is warm |
| `PANELAPP_LINK_DATA__REFRESH_INTERVAL` | `0` | Seconds between background refreshes of those lists (stale-while-revalidate); `0` disables the background task |
| `PANELAPP_LINK_DATA__GENE_BATCH_CAP` | `20` | Max gene symbols per `get_panels_for_genes` call (upstream politeness) |

`PREWARM` and `REFRESH_INTERVAL` are **off by default**, preserving the
no-network-at-boot posture; the production overlay turns both on. Freshness,
cache keys and the per-tool API mapping are in
[`data-lifecycle.md`](data-lifecycle.md).

## Rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE` | `0` (off) | Per-process MCP tool-call token bucket |

An opt-in politeness guard for unauthenticated public hosting: over the cap, a
call returns a structured `rate_limited` envelope and **never reaches upstream**,
so one client cannot induce heavy UK+AU fan-out and earn a 429 for everyone.

## Logging & tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `PANELAPP_LINK_LOG_FORMAT` | `console` | `console` or `json` (use `json` in containers) |
| `PANELAPP_LINK_OTEL__ENABLED` | `false` | Install an OTLP `TracerProvider` on startup (needs the `otel` extra) |
| `PANELAPP_LINK_OTEL__CONSOLE` | `false` | Also export spans to stderr (dev only; suppressed under `stdio` so it cannot corrupt the JSON-RPC channel) |

Prometheus RED metrics are always exposed at `GET /metrics` on the HTTP
transports; the OpenTelemetry spans are a no-op until an SDK + exporter is
configured. See [`architecture.md`](architecture.md#observability).
