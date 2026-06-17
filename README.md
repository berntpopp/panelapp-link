# PanelApp-Link

MCP + FastAPI server that grounds **gene-panel** questions in **PanelApp** —
a **pure live-API client** over **both** Genomics England PanelApp (UK) and
PanelApp Australia that answers panel/gene questions across either or both
regions, querying the public REST APIs per request with an in-memory cache.

A drop-in sibling of the `*-link` MCP fleet (e.g. `gencc-link`, `hgnc-link`).

> Research use only. **Not** for diagnosis, treatment, triage, patient
> management, or clinical decision support.

## Features

- **Both PanelApp regions** mirrored — Genomics England PanelApp (UK) and
  PanelApp Australia — selectable per call via `region` (`uk` | `australia` |
  `both`, default `both`).
- **All three entity types** — genes, regions (CNVs), and STRs (short tandem
  repeats), each with its traffic-light **confidence** (green / amber / red),
  mode of inheritance, and phenotypes.
- **Latest version + signed-off metadata** — every panel keeps its latest
  version plus `signed_off_version` / `signed_off_date` merged from the
  signed-off endpoint.
- **Cross-region gene roll-up** — fast gene-to-panels lookups aggregated across
  both regions (panel count, regions present, max confidence).
- **Server-side aggregation** — `compare_panels` diffs the genes of 2–5 panels
  (shared / only-in / confidence deltas), and `get_panels_for_genes` resolves
  panel membership for up to 20 gene symbols in one call.
- **Pure live-API client, no database** — queries the public PanelApp REST APIs
  per request and memoizes raw payloads in an in-memory TTL cache (default 6h),
  so the server is stateless: no SQLite mirror, no ingest, no build step.
- **9 MCP tools** with token-efficient `response_mode` shaping, typed
  `outputSchema`, plain-English headlines, and ready-to-call
  `_meta.next_commands` chains — on success **and** error envelopes, so recovery
  is deterministic.
- **Confidence filtering** — `min_confidence` filters by traffic-light rank
  (green = only green; amber = amber + green; red = all).
- **Observability** — every `_meta` carries `request_id` + `elapsed_ms`;
  `get_panelapp_diagnostics` reports the live sources, cache TTL, and cache stats.
- **Three transports** from one codebase: `unified` (REST + MCP), `http`, `stdio`.
- **Agent-discoverable** — `panelapp://` capabilities, usage, reference, license,
  citation, and research-use resources; typed error envelopes.

## Data sources & license

PanelApp exposes public, no-auth REST APIs for two regions; PanelApp-Link queries
both live, per request, with an in-memory cache — always serving the current
upstream data.

- **Sources:**
  - Genomics England PanelApp (UK) —
    [`panelapp.genomicsengland.co.uk/api/v1`](https://panelapp.genomicsengland.co.uk/api/v1)
  - PanelApp Australia —
    [`panelapp-aus.org/api/v1`](https://panelapp-aus.org/api/v1)
- **Data license:** PanelApp content is provided by Genomics England and the
  Australian Genomics PanelApp under their respective terms; consult each portal
  for attribution and reuse terms.
- **Not clinical:** PanelApp data is for research use only and is not intended for
  direct diagnostic use or medical decision-making without review by a genetics
  professional.

## Quick start

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project and dev dependencies
uv sync

# Start the unified REST + MCP server on http://127.0.0.1:8000
make dev

# Or start the local stdio MCP server (for Claude Desktop)
make mcp-serve
```

There is **no build step**. The server is a pure live-API client: it queries the
public PanelApp REST APIs on demand and caches raw payloads in memory (6h TTL by
default), so it is ready to serve as soon as it starts.

## Connecting Claude Code & Claude Desktop

Streamable HTTP at `/mcp` is recommended; stdio is a local fallback.

### Claude Code (HTTP)

```bash
make dev
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

Run the stdio server from a checkout with `uv` (no install step). The stdio entry
point is a live-API client and needs no data directory or build step. See
[`claude-desktop-config.json`](claude-desktop-config.json) for a ready-to-paste
block.

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

## Available MCP tools

| Tool | Purpose |
|------|---------|
| `search_panels` | Panel search (in-memory filter over name / disorders / disease group) across regions |
| `get_panel` | One panel's detail (`region` `uk`\|`australia`) + entity-count breakdown |
| `get_panel_genes` | A panel's entities (`entity_type` gene\|region\|str), filterable by `min_confidence` |
| `get_gene_panels` | All panels a gene appears on, across regions, grouped/sorted by confidence |
| `resolve_gene` | Resolve a symbol / HGNC id / free text to a gene (+ `matches[]` if ambiguous) |
| `get_server_capabilities` | Tool inventory, confidence vocab, entity types, regions, response modes, live sources |
| `get_panelapp_diagnostics` | Live sources, cache TTL, and in-memory cache stats |

Tools whose payloads vary accept `response_mode`: `minimal` | `compact`
(default) | `standard` | `full`, and the data tools accept `region`
(`uk` | `australia` | `both`). See [`docs/usage.md`](docs/usage.md) for the
canonical workflows and the citation contract.

## Architecture

PanelApp-Link is a **pure live-API client**. Each query calls the public PanelApp
REST APIs (1-2 calls per region) and memoizes the raw payloads in a small
in-memory **TTL cache** (default 6h), so repeated and related queries within the
window do not re-hit upstream. There is no database, no ingest, and no build step;
the server is stateless.

```
PanelApp UK + AU REST APIs (no auth)
  -> async REST client (concurrency cap, jittered backoff, honours Retry-After)
  -> service (per-query fetch + in-memory TTL cache; search filters in memory)
  -> MCP tools / envelope  +  FastAPI (/health, /, /docs)
  -> transports: unified | http | stdio
```

Full details, the per-tool API mapping, the cache, and an ASCII diagram are in
[`docs/architecture.md`](docs/architecture.md).

## Configuration

Settings load from environment variables prefixed `PANELAPP_LINK_` (nested data
config uses a double underscore) and an optional `.env` file. Copy
[`.env.example`](.env.example) and adjust. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PANELAPP_LINK_HOST` | `127.0.0.1` | Server host |
| `PANELAPP_LINK_PORT` | `8000` | Server port |
| `PANELAPP_LINK_TRANSPORT` | `unified` | `unified` \| `http` \| `stdio` |
| `PANELAPP_LINK_MCP_PATH` | `/mcp` | MCP endpoint path |
| `PANELAPP_LINK_LOG_LEVEL` | `INFO` | Logging level |
| `PANELAPP_LINK_LOG_FORMAT` | `console` | `console` or `json` |
| `PANELAPP_LINK_DATA__UK_API_URL` | `…genomicsengland.co.uk/api/v1` | UK PanelApp API base |
| `PANELAPP_LINK_DATA__AU_API_URL` | `…panelapp-aus.org/api/v1` | Australia PanelApp API base |
| `PANELAPP_LINK_DATA__REQUEST_TIMEOUT` | `60` | Per-request HTTP timeout (seconds) |
| `PANELAPP_LINK_DATA__MAX_CONCURRENCY` | `4` | Max concurrent API requests (kept low; PanelApp rate-limits bursts) |
| `PANELAPP_LINK_DATA__MAX_RETRIES` | `5` | Retries on 429/5xx/timeout (honours `Retry-After`) |
| `PANELAPP_LINK_DATA__USER_AGENT` | `PanelApp-Link/<version> …` | User-Agent sent to the PanelApp APIs |
| `PANELAPP_LINK_DATA__CACHE_SIZE` | `512` | In-memory cache entries (0 disables) |
| `PANELAPP_LINK_DATA__CACHE_TTL` | `21600` | In-memory cache TTL in seconds (default 6h) |

See [`docs/data-lifecycle.md`](docs/data-lifecycle.md) for data freshness, caching,
and how each tool maps to the live API endpoints.

## Development

```bash
make install      # install project + dev dependencies (uv sync --group dev)
make ci-local     # format-check, lint, file-size budget, typecheck, fast tests
make test         # run tests (excludes integration)
make test-cov     # run tests with coverage (gate: 85%)
make lint         # ruff lint
make lint-loc     # enforce the per-file line budget (scripts/check_file_size.py)
make typecheck    # mypy strict
```

`make ci-local` is the gate to run before every commit. The project uses `uv`,
Ruff (100 cols), mypy strict, and a per-file line budget enforced by
`scripts/check_file_size.py`. Integration tests (`-m integration`) hit the live
PanelApp APIs and are excluded from the default runs. Agentic coding tools should
follow `AGENTS.md`; Claude Code also loads the lean `CLAUDE.md`.

## Docker deployment

The image is **stateless** — a pure live-API client with no database and no
volume, so the container serves traffic almost immediately:

```bash
docker compose -f docker/docker-compose.yml up -d
curl http://localhost:8000/health
```

The host port defaults to `8000`; override it with `PANELAPP_LINK_HOST_PORT`
(e.g. `PANELAPP_LINK_HOST_PORT=9000 docker compose -f docker/docker-compose.yml
up -d`). The image sets a 6h cache TTL (`PANELAPP_LINK_DATA__CACHE_TTL=21600`).

For production, layer the prod overlay (no published ports, security hardening,
resource limits):

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

Data freshness and caching are documented in
[`docs/data-lifecycle.md`](docs/data-lifecycle.md).

## License & citation

- **Code:** MIT — see [`LICENSE`](LICENSE).
- **Data:** Genomics England PanelApp and PanelApp Australia, under their
  respective terms.

Cite PanelApp as:

> **Genomics England PanelApp** — Martin AR, Williams E, Foulger RE, et al.
> PanelApp crowdsources expert knowledge to establish consensus diagnostic gene
> panels. Nat Genet. 2019;51:1560-1565.
>
> **PanelApp Australia** — Australian Genomics PanelApp
> ([panelapp-aus.org](https://panelapp-aus.org)).

## Acknowledgments

- [Genomics England PanelApp](https://panelapp.genomicsengland.co.uk/) and
  [PanelApp Australia](https://panelapp-aus.org/), and the expert reviewers who
  curate the panels.
- [Model Context Protocol](https://modelcontextprotocol.io/),
  [FastMCP](https://github.com/jlowin/fastmcp),
  [FastAPI](https://fastapi.tiangolo.com/), and
  [Pydantic](https://pydantic.dev/).

---

**Research use only.** PanelApp-Link is a research tool and must not be used for
diagnosis, treatment, triage, patient management, or clinical decision support.
PanelApp data is not intended for direct diagnostic use or medical
decision-making without review by a genetics professional.
