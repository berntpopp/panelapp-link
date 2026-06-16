# AGENTS.md

Shared repository instructions for agentic coding tools working in PanelApp-Link.

## Project

PanelApp-Link is a Python FastAPI + MCP server that grounds gene-panel questions
in **PanelApp** data. It mirrors **both** PanelApp instances — Genomics England
PanelApp (UK) and PanelApp Australia — into a local read-only SQLite database and
answers panel/gene questions over either or both regions. PanelApp crowdsources
expert review to establish consensus diagnostic gene panels, classifying each
entity by a traffic-light confidence (green / amber / red). The server crawls the
public PanelApp REST APIs (no auth) at ingest time and serves the resulting
mirror in-process; the analytical value-add is cross-region gene roll-ups and
signed-off-version metadata alongside the latest panel version.

Primary areas:

- `panelapp_link/` - Python package: config, models, data store, services, MCP code
  - `api/` - async REST client used by ingest to crawl both regions
  - `ingest/` - crawl + build the SQLite mirror (`build` / `refresh` / `status`)
  - `data/` - schema.sql and the read-only SQLite repository
  - `services/` - panel/gene business logic, response shaping, refresh scheduler
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
  `panelapp_link/constants.py`; the SQLite schema in
  `panelapp_link/data/schema.sql`.

## Working Rules

- Do not revert or overwrite changes you did not make unless explicitly asked.
- Keep edits scoped to the task and avoid unrelated refactors.
- Prefer existing code patterns over new abstractions.
- Put tests under `tests/`; do not create alternate test roots.
- Use ASCII unless a file already requires non-ASCII content.
- Keep public hosted MCP tools read-only and research-use scoped.
- Be polite to upstream: bounded concurrency, jittered backoff, conditional
  refresh. Never crawl every `/panels/{id}/` in a tight loop; `refresh` re-fetches
  only changed/new panels.

## Commands

Required check before claiming completion:

- `make ci-local`

Useful focused commands:

- `make install` / `make lock`
- `make format` / `make lint` / `make lint-fix` / `make lint-loc`
- `make typecheck` / `make typecheck-fast`
- `make test` / `make test-fast` / `make test-unit` / `make test-integration`
- `make test-cov`
- `make data` / `make data-refresh` / `make data-status`
- `make dev` / `make mcp-serve`
- `make docker-build` / `make docker-up` / `make docker-down`

## Coding Standards

- Use `uv` for dependency management; do not use direct `pip` installs.
- Use modern Python typing: `list[str]`, `dict[str, int]`, `str | None`.
- Format and lint Python with Ruff (100-char line length).
- Type check with mypy targeting Python 3.12 (strict mode).
- Cover services and the repository with unit tests built from the committed
  fixtures under `tests/fixtures/`; use `respx` to mock the PanelApp APIs in tests.

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
- **Endpoints crawled:** `/panels/?page=N` (DRF paging), `/panels/signedoff/?page=N`
  (signed-off version + date, merged into panel rows by `id`), and
  `/panels/{id}/` for entity detail (`genes[]`, `regions[]`, `strs[]`).
- **Entity types:** `gene`, `region` (CNV), and `str` (short tandem repeat).
- **Confidence (traffic light):** `confidence_level` arrives as int or string;
  always cast to `str`. Map `"3"`/`"4"` -> green, `"2"` -> amber, `"1"`/`"0"` ->
  red. Ranks for filtering: green = 3, amber = 2, red = 1. `min_confidence`
  filters by rank (green = only green; amber = amber + green; red = all).
- **Versioning:** each panel keeps its **latest** version; `signed_off_version`
  and `signed_off_date` are recorded as metadata from `/panels/signedoff/`.
- **Identifiers:** gene = approved symbol or HGNC CURIE (e.g. `HGNC:1100`).
- Research use only; not for clinical decision support.
