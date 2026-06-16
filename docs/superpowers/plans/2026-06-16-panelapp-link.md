# panelapp-link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan workstream-by-workstream. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `panelapp-link`, a read-only MCP/API server that mirrors PanelApp (Genomics England UK + PanelApp Australia) into local SQLite and answers panel/gene questions, as a drop-in sibling of the `*-link` fleet.

**Architecture:** Ingest crawls both regions via an async httpx client into a read-only SQLite DB; MCP tools query SQLite only. All responses pass through the `run_mcp_tool` envelope with `response_mode` shaping, `_meta.next_commands`, citation provenance, and standard error codes. Two entrypoints: `server.py` (unified FastAPI + streamable-http MCP at `/mcp`) and `mcp_server.py` (stdio).

**Tech Stack:** Python 3.12+, uv, hatchling, FastMCP 3.2+, mcp SDK 1.27+, FastAPI, httpx, pydantic v2 + pydantic-settings, typer, structlog, sqlite3 (stdlib), pytest + respx, ruff, mypy strict.

**Reference (copy-and-adapt, do not reinvent):** `/home/bernt-popp/development/gencc-link` is the primary template (full ingest→SQLite→repo→service→MCP). `/home/bernt-popp/development/hgnc-link` is the template for `api/client.py` (live REST + jittered backoff). Spec: `docs/superpowers/specs/2026-06-16-panelapp-link-design.md`.

---

## Execution model

Workstreams W1–W8 each own disjoint files and can run as parallel subagents AFTER the W0 substrate is committed. Each workstream is TDD: write failing test → implement → green → commit. The integration barrier (W9) wires the facade + entrypoints and runs `make ci-local`.

```
W0 substrate (BARRIER, build first)
  ├─ W1 api/client            ┐
  ├─ W2 data/schema+repository │ parallel (depend only on W0)
  ├─ W3 ingest                 │
  ├─ W4 services + shaping     │
  ├─ W5 mcp envelope/errors    │
  ├─ W6 mcp capabilities/resources/next_commands/schemas/annotations
  ├─ W7 mcp tools (panels/genes/discovery)  (depends on W4,W5,W6 interfaces)
  └─ W8 docker/CI/docs/AGENTS/README/LICENSE
W9 integration barrier: facade, server_manager, server.py, mcp_server.py, ci-local green, docker build
```

Interfaces are frozen in W0 (enums, exceptions, config field names) and in the spec (DB columns, dict payload keys). Agents must import from those, not invent.

---

## W0 — Shared substrate (build first, barrier)

**Files:** `pyproject.toml`, `panelapp_link/__init__.py`, `config.py`, `constants.py`, `exceptions.py`, `logging_config.py`, `models/enums.py`, `models/records.py`, `data/schema.sql`, `scripts/check_file_size.py`, `.loc-allowlist`, `Makefile`, `.pre-commit-config.yaml`, `.env.example`, `.env.docker.example`, `tests/conftest.py`.

- [ ] Copy `pyproject.toml` from gencc-link; rename `gencc`→`panelapp`, scripts → `panelapp-link`/`-mcp`/`-data`, package `panelapp_link`, per-file ruff ignores for `data/repository.py`, `ingest/builder.py` (S608). Coverage `fail_under = 85`.
- [ ] `__init__.py`: `__version__ = "0.1.0"`.
- [ ] `exceptions.py`: `PanelAppError(message)` base; `InvalidInputError(message, field=None)`; `NotFoundError`; `AmbiguousQueryError(message, candidates=None)`; `DataUnavailableError`; `DownloadError(message, status_code=None)`; `RateLimitError(DownloadError)`. (Mirror gencc names; `McpToolError` lives in envelope.)
- [ ] `models/enums.py`: `ResponseMode`, `Region`, `EntityType`, `ConfidenceLabel` literals + `RESPONSE_MODES`, `REGIONS`, `ENTITY_TYPES`, `CONFIDENCE_LABELS` tuples.
- [ ] `constants.py`: `CONFIDENCE_TO_LABEL = {"4":"green","3":"green","2":"amber","1":"red","0":"red"}`, `CONFIDENCE_RANK = {"green":3,"amber":2,"red":1}`, `REGION_LABELS = {"uk":"Genomics England PanelApp","australia":"PanelApp Australia"}`, `REGION_BASE_KEYS`, citation strings, `SCHEMA_VERSION = "1"`. Add `confidence_label(level:str)->str` and `confidence_rank_for_label(label)->int` helpers.
- [ ] `config.py`: copy gencc; env prefix `PANELAPP_LINK_`; `PanelAppDataConfigModel` fields per spec §11; `ServerSettings` with `data` nested. `settings` singleton + `get_data_config()`.
- [ ] `logging_config.py`: copy gencc verbatim (rename logger name).
- [ ] `models/records.py`: `PanelSummary`, `PanelDetail`, `Entity` (single model, `entity_type` + common fields + `extra: dict`), `GenePanelHit`, `GeneSummary`, `BuildMeta` per spec §6.
- [ ] `data/schema.sql`: exact tables/indexes from spec §5.
- [ ] `scripts/check_file_size.py` + `.loc-allowlist`: copy gencc (600-line budget).
- [ ] `Makefile`, `.pre-commit-config.yaml`, `.env*.example`: copy gencc, rename.
- [ ] **Test:** `tests/test_config.py` (settings load, env prefix, db_path), `tests/test_constants.py` (confidence map both directions). `conftest.py`: fixture loaders for `tests/fixtures/*.json`, temp-DB builder stub (filled by W3), `set_service_for_testing` hook (filled by W5/W7).
- [ ] Commit: `feat: scaffold panelapp-link substrate (config, models, enums, schema)`.

## W1 — `api/client.py`

**Files:** Create `panelapp_link/api/__init__.py`, `api/client.py`; Test `tests/test_api_client.py`.

- [ ] Adapt hgnc-link `api/client.py`. Class `PanelAppRestClient(config, *, client=None)`: `asyncio.Semaphore(max_concurrency)`, injectable `httpx.AsyncClient`, `Accept: application/json`, `User-Agent` from config, `_RETRYABLE_STATUS={429,500,502,503,504}`, backoff `min(0.5*2**attempt, 8.0)` with `random.uniform(0,delay)`.
- [ ] Methods: `async list_panels(base_url) -> AsyncIterator[dict]` (follow DRF `next`), `async list_signed_off(base_url) -> AsyncIterator[dict]`, `async get_panel(base_url, panel_id) -> dict`, `async aclose()`. 403/429→`RateLimitError`; exhausted retries on 5xx/timeout→`DownloadError`.
- [ ] **Tests (respx):** paginated `list_panels` yields all results across 2 mocked pages; `get_panel` returns detail; 429 raises `RateLimitError`; 503 retried then `DownloadError`. Use fixtures `uk_panels_page1.json`.
- [ ] Commit.

## W2 — `data/repository.py`

**Files:** Create `panelapp_link/data/__init__.py`, `data/repository.py`; Test `tests/test_repository.py`.

- [ ] Adapt hgnc-link `data/repository.py`. `PanelAppRepository(db_path)`: open `file:{path}?mode=ro`, `row_factory=Row`; raise `DataUnavailableError` if missing/unreadable.
- [ ] Query methods (return plain dict rows; decode `_json` columns): `get_meta()`; `search_panels(query, regions, limit, offset)` via `panel_fts` MATCH with sanitized query + LIKE fallback; `get_panel(region, panel_id)`; `get_panel_entities(region, panel_id, entity_type, min_rank, limit, offset)`; `get_gene_panels(symbol_upper|hgnc_id, regions, min_rank)`; `resolve_gene(symbol_upper|hgnc_id)`; `get_gene(symbol_upper)`. Region filter accepts `["uk","australia"]`.
- [ ] **Tests:** build temp DB from fixtures (use W3 builder once available; until then a minimal inline INSERT helper in conftest). Assert search hit, panel fetch, entity filter by confidence rank, gene→panels across regions, not-found returns None.
- [ ] Commit.

## W3 — `ingest/` (downloader, builder, cli, lock)

**Files:** Create `panelapp_link/ingest/{__init__,downloader,builder,cli,lock}.py`; Tests `tests/test_ingest.py`, `tests/test_cli.py`, `tests/test_data_lifecycle.py`.

- [ ] `lock.py`: copy gencc cross-process lock.
- [ ] `downloader.py`: `async crawl_region(client, region, base_url) -> dict` — collect panels (list), signed-off map `{id:{version,signed_off}}`, and detail per panel id; returns `{"panels":[...], "signed_off":{...}, "details":{id:detail}}`. `async crawl_all(config) -> dict[region]`.
- [ ] `builder.py`: `build_database(config, crawled) -> BuildMeta` — write tmp DB, `executescript(schema)`, insert panels (merge signed-off), explode entities (genes+regions+strs; cast confidence to str, derive label+rank; pack region/str extras into `extra_json`), roll up `gene` table, build FTS, write `meta` (incl. `panel_versions_json`), atomic swap. `refresh(config, force)` reuses stored versions for incremental fetch.
- [ ] `cli.py`: typer app `build` / `refresh` / `status`; `main()`. Maps `DownloadError`/`RateLimitError`→exit 1 with message.
- [ ] **Tests:** feed captured fixtures (no live calls) → build temp DB → assert panel count, entity-type split (gene/region/str all present from `uk_panel_285.json`), confidence labels, signed-off merge, gene roll-up across regions. `test_cli` runs `status` on built DB. Mark any live test `integration`.
- [ ] Commit.

## W4 — `services/` (panelapp_service, shaping, refresh)

**Files:** Create `panelapp_link/services/{__init__,panelapp_service,shaping,refresh}.py`; Tests `tests/test_service.py`, `tests/test_shaping.py`.

- [ ] `shaping.py`: `shape_panel(row, mode)`, `shape_entity(row, mode)`, `shape_gene_panel_hit(row)`, `shape_gene(row)` — trim by `response_mode` per spec §7. Pure functions over repo dict rows.
- [ ] `panelapp_service.py`: `PanelAppService(repository, *, cache_size, cache_ttl)` with `_TTLCache` (copy gencc). Methods returning plain dicts (payload only, no envelope): `search_panels`, `get_panel`, `get_panel_genes`, `get_gene_panels`, `resolve_gene`, `capabilities_data()`, `diagnostics()`. Validates `response_mode`/`limit`/region; raises typed exceptions. Region `"both"` → query `["uk","australia"]` and merge.
- [ ] `refresh.py`: copy gencc in-process scheduler (conditional refresh interval+jitter).
- [ ] **Tests:** service over temp DB asserts payload keys per mode (minimal vs full), region=both merges, invalid mode→`InvalidInputError`, missing gene→`NotFoundError`, ambiguous symbol→`AmbiguousQueryError`.
- [ ] Commit.

## W5 — `mcp/envelope.py` + `middleware.py` + `annotations.py`

**Files:** Create those three; Test `tests/test_envelope.py`.

- [ ] Adapt gencc `envelope.py`: `McpErrorContext`, `McpToolError(error_code,message)`, `run_mcp_tool(tool_name, call, *, context, response_mode)`, `_classify`, `_provenance_meta` (citation_ref `panelapp://citation`, citation_short, `unsafe_for_clinical_use=True`, mode-aware), `_error_envelope`. Wire `recovery_commands` from W6.
- [ ] `annotations.py`: `READ_ONLY_OPEN_WORLD = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)`.
- [ ] `middleware.py`: copy gencc `InputValidationMiddleware`.
- [ ] **Tests:** success envelope adds `success`,`_meta.request_id`,`elapsed_ms`; each exception type → correct `error_code`/`retryable`; pydantic ValidationError → `invalid_input` with `field_errors`.
- [ ] Commit.

## W6 — `mcp/` discovery surface (capabilities, resources, next_commands, schemas)

**Files:** Create `mcp/capabilities.py`, `resources.py`, `next_commands.py`, `schemas.py`; Tests `tests/test_capabilities.py`, `tests/test_next_commands.py`.

- [ ] `resources.py`: static text — usage notes, reference (confidence labels/ranks, entity types, regions, paging contract), license (PanelApp terms + research-use), citation (Martin 2019 + PanelApp AU verbatim), research-use notice.
- [ ] `schemas.py`: `tool_output_schema(**fields)` helper + `SEARCH_PANELS_SCHEMA`, `GET_PANEL_SCHEMA`, `GET_PANEL_GENES_SCHEMA`, `GET_GENE_PANELS_SCHEMA`, `RESOLVE_GENE_SCHEMA`, `CAPABILITIES_SCHEMA`, `DIAGNOSTICS_SCHEMA`.
- [ ] `capabilities.py`: `build_capabilities()` (server, version, tools, vocab: confidence labels+ranks/entity types/regions, response_modes, workflows, error_codes, resources map, `capabilities_version` content hash, live `data` freshness from repo meta) + `register_capability_resources(mcp)` registering the 6 `panelapp://` resources.
- [ ] `next_commands.py`: `cmd(tool,**args)`; `after_search_panels(panels)` → `get_panel`/`get_panel_genes`; `after_resolve_gene(gene)` → `get_gene_panels`; `after_get_panel(...)` → `get_panel_genes`; `recovery_commands(tool,error_code,args,field)`.
- [ ] **Tests:** `capabilities_version` stable across calls; all 7 tools listed; resources resolve to non-empty strings; next_command builders return correct tool refs.
- [ ] Commit.

## W7 — `mcp/tools/` (panels, genes, discovery) + `_args`, `service_adapters`

**Files:** Create `mcp/service_adapters.py`, `mcp/tools/{__init__,_args,panels,genes,discovery}.py`; Test `tests/test_tools.py`, `tests/test_tool_naming.py`.

- [ ] `service_adapters.py`: `get_panelapp_service()` singleton (hot-reload on db mtime) + `set_service_for_testing(svc)`.
- [ ] `_args.py`: `normalize_region(region)->list[str]`, `coalesce_gene(gene_symbol,hgnc_id,query,required)`, `validate_min_confidence`, `validate_entity_type`.
- [ ] `panels.py`: `register_panel_tools(mcp)` with `search_panels`, `get_panel`, `get_panel_genes` — decorator (name,title,annotations,output_schema,tags,description), inner `call()` → service → attach `_meta.next_commands` (paging + follow-ups) → `run_mcp_tool`.
- [ ] `genes.py`: `register_gene_tools(mcp)` with `get_gene_panels`, `resolve_gene`.
- [ ] `discovery.py`: `register_discovery_tools(mcp)` with `get_server_capabilities`, `get_panelapp_diagnostics`.
- [ ] **Tests:** each tool returns `success:true` + expected keys over temp-DB-backed service; `region="both"` merges; bad input → `invalid_input` envelope; tool names match snake_case allowlist (`test_tool_naming`).
- [ ] Commit.

## W8 — packaging: docker, CI, docs, AGENTS, README, LICENSE

**Files:** `docker/{Dockerfile,docker-compose.yml,docker-compose.prod.yml,entrypoint.sh}`, `.dockerignore`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `LICENSE`, `docs/architecture.md`, `docs/usage.md`, `docs/data-lifecycle.md`, `claude-desktop-config.json`.

- [ ] Copy gencc `docker/` multi-stage; entrypoint runs `panelapp-link-data refresh` before serving; volume `/app/data`; healthcheck `/health`.
- [ ] `.github/workflows/ci.yml`: Python 3.12 + uv + `uv sync --group dev --frozen` + `make ci-local` + `make test-cov`.
- [ ] `AGENTS.md`/`CLAUDE.md`/`README.md`: adapt gencc, PanelApp domain notes (regions, endpoints, confidence semantics, signed-off, research-use). `LICENSE` MIT. `CHANGELOG.md` 0.1.0. `claude-desktop-config.json` stdio entry.
- [ ] **Test:** `tests/test_app.py` imports server modules and asserts FastAPI app + MCP mount exist (after W9). `docker build` succeeds locally.
- [ ] Commit.

## W9 — Integration barrier

**Files:** `mcp/facade.py`, `panelapp_link/server_manager.py`, `server.py`, `mcp_server.py`.

- [ ] `facade.py`: `create_panelapp_mcp() -> FastMCP` — instantiate FastMCP, add middleware, `register_panel_tools`/`register_gene_tools`/`register_discovery_tools`, `register_capability_resources`.
- [ ] `server_manager.py`, `server.py`, `mcp_server.py`: copy gencc, rename. argparse transport switch; stdio sets env defaults.
- [ ] Run `make ci-local` and `make test-cov`; fix failures. Run `panelapp-link-data build` (live, once) to verify a real DB builds; mark as manual/integration. `docker build`.
- [ ] Final commit + tag readiness.

---

## Self-review notes
- **Spec coverage:** every spec section maps to a workstream (arch→all; schema→W0/W2/W3; models→W0; tools→W7; envelope/errors→W5; resources/capabilities→W6; api→W1; ingest→W3; config→W0; testing→each; DoD→W9).
- **Type consistency:** confidence helpers defined once in `constants.py` (W0), imported by W3/W4. Service returns plain dicts; tools attach `_meta` then call `run_mcp_tool` (gencc contract). Region normalization centralized in `_args.normalize_region` (W7) and `service` (W4) — both map `both→["uk","australia"]`.
- **No placeholders:** field lists deferred to spec §5/§6/§7 by reference (engineer reads spec), exact per-file reference templates named.
