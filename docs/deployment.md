# Deployment

PanelApp-Link is a **pure live-API client**: no database, no ingest, no build
step and no data volume. The container is stateless and serves traffic almost
immediately — there is nothing to bootstrap, and the upstream PanelApp APIs need
no API key.

Every variable named below is documented in
[`configuration.md`](configuration.md).

## Connecting an MCP client

Streamable HTTP at `/mcp` is the recommended transport; `stdio` is a local
fallback.

### Claude Code (HTTP)

```bash
make dev   # unified REST + MCP on http://127.0.0.1:8000
claude mcp add --transport http panelapp-link http://127.0.0.1:8000/mcp
```

### Claude Desktop (HTTP)

```json
{
  "mcpServers": {
    "panelapp-link": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### Claude Desktop (stdio)

Run the stdio server straight from a checkout with `uv` — no install step, no
data directory, no build step. [`claude-desktop-config.json`](../claude-desktop-config.json)
in the repo root is a ready-to-paste block.

```json
{
  "mcpServers": {
    "panelapp-link": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/panelapp-link", "run", "panelapp-link-mcp"]
    }
  }
}
```

## Docker (development stack)

```bash
docker compose -f docker/docker-compose.yml up -d
curl http://localhost:8000/health
```

The base stack binds the published port to **loopback only**
(`127.0.0.1:<port>:8000`) so copying the file to a server can never publish this
unauthenticated backend on the public IP. The host port defaults to `8000` and is
overridden with `PANELAPP_LINK_HOST_PORT`:

```bash
PANELAPP_LINK_HOST_PORT=9000 docker compose -f docker/docker-compose.yml up -d
```

The image runs the `unified` transport with JSON logging and a 6h cache TTL
(`PANELAPP_LINK_DATA__CACHE_TTL=21600`) baked in. Because the server is
stateless, the healthcheck uses a short `start_period`.

Make targets: `make docker-build`, `make docker-up`, `make docker-down`,
`make docker-logs`, `make docker-prod-config`.

## Docker (production)

Layer the prod overlay on the base file:

```bash
export PANELAPP_LINK_IMAGE=ghcr.io/berntpopp/panelapp-link@sha256:<digest>
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

The overlay follows the fleet Container & Deployment Hardening standard:

- **digest-pinned image** — `PANELAPP_LINK_IMAGE` is required and takes a
  `@sha256:` digest, not a floating tag;
- **no published host ports** (`ports: !reset []`, `expose: 8000`) — the
  container is reachable only through the reverse proxy. Backends are
  unauthenticated by design and MUST NOT be published directly;
- `no-new-privileges`, `cap_drop: ALL`, `read_only: true` root filesystem with a
  size-capped `tmpfs` for `/tmp` (the only writable path a live-API client
  needs), `init: true`, and a memory limit;
- **Host/Origin allowlists carry the public hostname** (e.g.
  `panelapp-link.genefoundry.org`) alongside the loopback defaults, in
  `PANELAPP_LINK_ALLOWED_HOSTS`, `PANELAPP_LINK_ALLOWED_ORIGINS` **and**
  `PANELAPP_LINK_CORS_ORIGINS` — omitting the first rejects every proxied
  request;
- **warm cache** — `PANELAPP_LINK_DATA__PREWARM=true` plus
  `PANELAPP_LINK_DATA__REFRESH_INTERVAL=3600` keep the heavy panel lists warm
  (single worker ⇒ one shared process cache), with a larger `CACHE_SIZE` and a
  shorter `CACHE_TTL` for a busier, fresher cache;
- **rate limit on** — `PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE=120` caps MCP
  tool calls so one anonymous client cannot induce heavy UK+AU fan-out.

### Nginx Proxy Manager

`docker/docker-compose.npm.yml` is **self-contained** (used standalone, not
layered on the base file): one unified container, no published host ports, joined
to the shared external `npm_default` network and reverse-proxied by container
name.

```bash
docker compose -f docker/docker-compose.npm.yml --env-file .env.docker up -d --build
```

## Health & observability

- `GET /health` — liveness; the Compose healthcheck sends `Host: localhost` so it
  passes the Host allowlist.
- `GET /metrics` — Prometheus RED metrics (request rate, errors by code, tool and
  per-region upstream latency percentiles, cache hit ratio).
- `get_panelapp_diagnostics` — the live upstream sources, cache TTL and cache
  stats, over MCP.

Configure an OpenTelemetry SDK + exporter to activate the tool/upstream spans;
they are a no-op otherwise. See [`architecture.md`](architecture.md#observability).
