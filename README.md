# PanelApp-Link

MCP + FastAPI server that grounds **gene-panel** questions in **PanelApp** —
mirroring **both** Genomics England PanelApp (UK) and PanelApp Australia into a
local read-only SQLite database and answering panel/gene questions across either
or both regions.

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
- **Local SQLite + FTS5 store** built by an async crawl of the public PanelApp
  REST APIs — fast, deterministic, no upstream API at query time.
- **7 MCP tools** with token-efficient `response_mode` shaping, typed
  `outputSchema`, plain-English headlines, and ready-to-call
  `_meta.next_commands` chains — on success **and** error envelopes, so recovery
  is deterministic.
- **Confidence filtering** — `min_confidence` filters by traffic-light rank
  (green = only green; amber = amber + green; red = all).
- **Observability** — every `_meta` carries `request_id` + `elapsed_ms`;
  `get_panelapp_diagnostics` reports build provenance and per-region freshness.
- **Three transports** from one codebase: `unified` (REST + MCP), `http`, `stdio`.
- **Agent-discoverable** — `panelapp://` capabilities, usage, reference, license,
  citation, and research-use resources; typed error envelopes.

## Data sources & license

PanelApp exposes public, no-auth REST APIs for two regions; PanelApp-Link crawls
both at ingest time and serves a local mirror.

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

# Crawl both PanelApp regions and build the local SQLite database
make data            # == panelapp-link-data build

# Start the unified REST + MCP server on http://127.0.0.1:8000
make dev

# Or start the local stdio MCP server (for Claude Desktop)
make mcp-serve
```

The database is built into `<repo>/data/panelapp.sqlite` by default. With
`PANELAPP_LINK_DATA__AUTO_BOOTSTRAP=true` (the default), the HTTP / unified server
also builds the database on first use if it is absent, so `make data` is optional
but recommended for a predictable first boot.

Database management commands:

```bash
make data          # panelapp-link-data build   — force full crawl + rebuild
make data-refresh  # panelapp-link-data refresh — incremental: only changed/new panels
make data-status   # panelapp-link-data status  — print build provenance
```

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
point crawls both regions and builds the local database on first start. See
[`claude-desktop-config.json`](claude-desktop-config.json) for a ready-to-paste
block.

```json
{
  "mcpServers": {
    "panelapp-link": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/panelapp-link", "run", "panelapp-link-mcp"],
      "env": {
        "PANELAPP_LINK_DATA__DATA_DIR": "/absolute/path/to/panelapp-link/data"
      }
    }
  }
}
```

## Available MCP tools

| Tool | Purpose |
|------|---------|
| `search_panels` | Ranked panel search (FTS over name / disorders / disease group) across regions |
| `get_panel` | One panel's detail (`region` `uk`\|`australia`) + entity-count breakdown |
| `get_panel_genes` | A panel's entities (`entity_type` gene\|region\|str), filterable by `min_confidence` |
| `get_gene_panels` | All panels a gene appears on, across regions, grouped/sorted by confidence |
| `resolve_gene` | Resolve a symbol / HGNC id / free text to a gene (+ `matches[]` if ambiguous) |
| `get_server_capabilities` | Tool inventory, confidence vocab, entity types, regions, response modes, data freshness |
| `get_panelapp_diagnostics` | Build provenance + per-region panel counts and freshness |

Tools whose payloads vary accept `response_mode`: `minimal` | `compact`
(default) | `standard` | `full`, and the data tools accept `region`
(`uk` | `australia` | `both`). See [`docs/usage.md`](docs/usage.md) for the
canonical workflows and the citation contract.

## Architecture

PanelApp publishes public, slow-changing REST data for two regions, so
PanelApp-Link crawls both once into a local **SQLite + FTS5** artifact and queries
it in-process — no upstream client, rate limiting, or caching against the live
APIs at query time.

```
ingest (crawl UK + AU -> merge signed-off -> build) -> SQLite + FTS5 store
  -> repository (read-only) -> service (search / panels / genes / roll-ups)
  -> MCP tools  +  FastAPI (/health, /, /docs)
  -> transports: unified | http | stdio
```

Full details, the ingest crawl, the signed-off merge, and an ASCII diagram are in
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
| `PANELAPP_LINK_DATA__DATA_DIR` | `<repo>/data` | Directory for the built database |
| `PANELAPP_LINK_DATA__DB_FILENAME` | `panelapp.sqlite` | SQLite filename in the data dir |
| `PANELAPP_LINK_DATA__REQUEST_TIMEOUT` | `60` | Per-request HTTP timeout (seconds) |
| `PANELAPP_LINK_DATA__MAX_CONCURRENCY` | `8` | Max concurrent crawl requests |
| `PANELAPP_LINK_DATA__MAX_RETRIES` | `4` | Retries on 429/5xx/timeout |
| `PANELAPP_LINK_DATA__AUTO_BOOTSTRAP` | `true` (image: `false`) | Build the database lazily on first use if absent |
| `PANELAPP_LINK_DATA__REFRESH_ENABLED` | `true` | Run the in-app conditional-refresh scheduler (unified/http only) |
| `PANELAPP_LINK_DATA__REFRESH_INTERVAL_HOURS` | `24` | Hours between conditional refresh checks |
| `PANELAPP_LINK_DATA__REFRESH_JITTER_SECONDS` | `300` | Random jitter added to each refresh |
| `PANELAPP_LINK_DATA__BUILD_LOCK_TIMEOUT` | `600` | Seconds to wait for the cross-process build lock |
| `PANELAPP_LINK_DATA__CACHE_SIZE` | `512` | Query cache entries (0 disables) |
| `PANELAPP_LINK_DATA__CACHE_TTL` | `3600` | Query cache TTL (seconds) |

See [`docs/data-lifecycle.md`](docs/data-lifecycle.md) for how the database is
built on startup and refreshed on a schedule.

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

```bash
make docker-build           # build the image
make docker-up              # start the unified server on host port 8000
curl http://localhost:8000/health
make docker-logs
make docker-down
```

The container's **entrypoint crawls both regions and builds the database once on
startup** (before the server accepts traffic), and an **in-app scheduler**
conditionally refreshes it every 24h — re-listing panels and re-fetching only the
changed/new ones — and hot-reloads the running server, so first-request latency is
predictable. The built database lives in the `panelapp-data` named volume at
`/app/data` and persists across restarts.

For production, layer the prod overlay (no published ports, security hardening,
resource limits):

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

The full strategy and all scheduling options are documented in
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
