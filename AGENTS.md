# AGENTS.md

Shared repository instructions for agentic coding tools working in PanelApp-Link.

## Project

PanelApp-Link is a Python FastAPI + MCP server that grounds gene-panel questions
in **PanelApp** data. It is a **pure live-API client** over **both** PanelApp
instances — Genomics England PanelApp (UK) and PanelApp Australia — and answers
panel/gene questions over either or both regions. PanelApp crowdsources expert
review to establish consensus diagnostic gene panels, classifying each entity by
a traffic-light confidence (green / amber / red). The server queries the public
PanelApp REST APIs (no auth) per request and memoizes raw payloads in an
in-memory TTL cache; there is no local database, ingest, or build step. The
analytical value-add is cross-region gene roll-ups and signed-off-version
metadata alongside the latest panel version.

Primary areas:

- `panelapp_link/` - Python package: config, models, services, MCP code
  - `api/` - async REST client (`client.py`) that queries both regions live at
    request time
  - `services/` - async panel/gene business logic (`panelapp_service.py`) with an
    in-memory TTL cache, pure transform helpers (`_live_helpers.py`), and
    response shaping (`shaping.py`)
  - `mcp/` - facade, tools, capabilities, envelope, next_commands, resources
- `tests/` - unit and integration tests; fixtures under `tests/fixtures/`
- `docker/` - Dockerfile and Compose deployment files
- `docs/` - architecture, usage, data lifecycle, and design specs (`docs/superpowers/`)

## Source Of Truth

- Use this file for shared repo-wide agent guidance.
- Keep `CLAUDE.md` lean and Claude-specific; it should reference this file.
- Prefer `Makefile` targets over ad hoc commands.
- Use `uv.lock` as the dependency lock source of truth.
- Confidence maps, ranks, region labels, and citations live in
  `panelapp_link/constants.py`.

## Working Rules

- Do not revert or overwrite changes you did not make unless explicitly asked.
- Keep edits scoped to the task and avoid unrelated refactors.
- Prefer existing code patterns over new abstractions.
- Put tests under `tests/`; do not create alternate test roots.
- Use ASCII unless a file already requires non-ASCII content.
- Keep public hosted MCP tools read-only and research-use scoped.
- Be polite to upstream: queries are live, so keep concurrency low (default 4),
  use jittered backoff, honour `Retry-After`, and lean on the in-memory cache.
  Never fan out to every `/panels/{id}/` in a tight loop; PanelApp rate-limits
  aggressive per-IP bursts with HTTP 429.

## Commands

Required check before claiming completion:

- `make ci-local`

Useful focused commands:

- `make install` / `make lock`
- `make format` / `make lint` / `make lint-fix` / `make lint-loc`
- `make typecheck` / `make typecheck-fast`
- `make test` / `make test-fast` / `make test-unit` / `make test-integration`
- `make test-cov`
- `make dev` / `make mcp-serve`
- `make docker-build` / `make docker-up` / `make docker-down`

## Coding Standards

- Use `uv` for dependency management; do not use direct `pip` installs.
- Use modern Python typing: `list[str]`, `dict[str, int]`, `str | None`.
- Format and lint Python with Ruff (100-char line length).
- Type check with mypy targeting Python 3.12 (strict mode).
- Cover the service and helpers with unit tests; use `respx` to mock the live
  PanelApp APIs (committed fixtures under `tests/fixtures/`). Tests must never hit
  the network except under the `integration` marker.

## File Size Discipline

Hard cap: **600 lines per Python module** in `panelapp_link/`, `server.py`, and
`mcp_server.py`. Enforced by `make lint-loc`, wired into `make ci-local`. Tests
are exempt. When a file approaches 500 lines, plan a cohesive split before
adding more behavior. Grandfather only via `.loc-allowlist`.

## Testing Notes

- `make test` is the fast default (excludes integration).
- `make test-fast` runs in parallel via pytest-xdist.
- `make test-cov` runs coverage; gate is 85%.
- Markers: `unit`, `integration`, `mcp`, `slow`. Integration tests hit the live
  PanelApp APIs; run them sparingly.

## PanelApp Domain Notes

- **Regions / base URLs (no auth):**
  - UK — `https://panelapp.genomicsengland.co.uk/api/v1`
  - Australia — `https://panelapp-aus.org/api/v1`
  - The `region` argument is `uk` | `australia` | `both` (default `both`).
- **Live API per query (DRF paging on `next`):**
  - `get_gene_panels` / `resolve_gene` → `GET /genes/?entity_name=SYMBOL`
    (one call per region; each result carries the full `panel` object).
  - `get_panel` / `get_panel_genes` → `GET /panels/{id}/` (entity detail:
    `genes[]`, `regions[]`, `strs[]`).
  - `search_panels` fetches the cached panel list (`GET /panels/`) and filters in
    memory — PanelApp has no usable server-side panel search.
  - Signed-off version + date come from the cached `GET /panels/signedoff/`,
    merged into panel rows by `id`.
- **Caching / rate limits:** raw payloads are memoized in an in-memory TTL cache
  (default 6h) with **single-flight coalescing** (`services/cache.py`) so
  concurrent identical fetches share one upstream call. PanelApp rate-limits
  aggressive per-IP bursts with HTTP 429 and sends `Retry-After`, which the
  client honours; keep concurrency low. Optional warm-up:
  `PANELAPP_LINK_DATA__PREWARM=true` and `…__REFRESH_INTERVAL=<seconds>` (both
  default off). Optional MCP throttle:
  `PANELAPP_LINK_MCP_RATE_LIMIT_PER_MINUTE=<n>` (0 = off).
- **Observability:** `panelapp_link/observability/` has three layers — per-call
  `_meta` breadcrumbs (`request_id`, `elapsed_ms`, `cache`, per-region
  `upstream`), process-wide RED metrics (Prometheus at `GET /metrics` +
  `get_panelapp_diagnostics`), and OpenTelemetry spans (no-op until an SDK +
  exporter is configured). The MCP envelope (`run_mcp_tool`) is the single choke
  point that wires all three; keep new tool work flowing through it so it stays
  instrumented.
- **Entity types:** `gene`, `region` (CNV), and `str` (short tandem repeat).
- **Confidence (traffic light):** `confidence_level` arrives as int or string;
  always cast to `str`. Map `"3"`/`"4"` -> green, `"2"` -> amber, `"1"`/`"0"` ->
  red. Ranks for filtering: green = 3, amber = 2, red = 1. `min_confidence`
  filters by rank (green = only green; amber = amber + green; red = all).
- **Versioning:** each panel keeps its **latest** version; `signed_off_version`
  and `signed_off_date` are read from `/panels/signedoff/`.
- **Identifiers:** gene = approved symbol or HGNC CURIE (e.g. `HGNC:1100`).
- Research use only; not for clinical decision support.
