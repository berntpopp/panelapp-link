# panelapp-link — Design Spec

**Status:** approved 2026-06-16 · **Author:** systems engineering · **Mission:** drop-in sibling of the `*-link` fleet that grounds gene-panel questions in PanelApp data (Genomics England UK **and** PanelApp Australia).

> Historical record — this design records the proposed system as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

## 1. Goal & scope

A read-only MCP/API server that mirrors PanelApp into local SQLite and answers panel/gene questions over both regions. It must be idiomatically identical to `gencc-link` / `hgnc-link`: same stack, layout, envelope, response modes, resources, ingest CLI, tooling, Docker, CI.

**Decisions locked (user-approved):**
- **Data architecture:** local **SQLite ingest mirror** (fleet pattern). MCP tools query SQLite only — no live API calls at request time. `api/` is used by ingest.
- **Versioning:** capture each panel's **latest** version; record `signed_off_version` + `signed_off_date` as metadata (merged from `/panels/signedoff/`).
- **Scope:** **full mirror, both regions, all three entity types** (gene, region/CNV, str).

**Out of scope (YAGNI):** evaluations endpoint, panel-version history, write/curation, gene-of-the-day, auth, live request-time fallback.

## 2. Stack & conventions (mirror siblings exactly)

- Python ≥3.12, `uv`, hatchling. `fastmcp>=3.2,<4`, `mcp[cli]>=1.27.2,<2`, `fastapi`, `uvicorn[standard]`, `pydantic>=2.11`, `pydantic-settings>=2.6`, `httpx>=0.28`, `structlog`, `orjson`, `rich`, `typer`, `gunicorn`.
- Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `pytest-xdist`, `respx`, `ruff>=0.8`, `mypy>=1.14`, `pre-commit`.
- ruff line-length 100, target py312, extend-select `E,W,F,I,N,UP,B,C4,S,T20,SIM,RUF`, Google docstrings; mypy **strict**; coverage **fail_under = 85**.
- Entry points (`[project.scripts]`): `panelapp-link = "server:main"`, `panelapp-link-mcp = "mcp_server:main"`, `panelapp-link-data = "panelapp_link.ingest.cli:main"`.
- `hatch.build.targets.wheel` packages `["panelapp_link"]`, force-include `server.py` + `mcp_server.py`.
- Env prefix `PANELAPP_LINK_`, nested delimiter `__` (e.g. `PANELAPP_LINK_DATA__DATA_DIR`).
- 600-line per-file budget enforced by `scripts/check_file_size.py` + `.loc-allowlist`.
- Makefile targets: `install lock upgrade format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fast test test-fast test-unit test-integration test-cov check ci-local precommit clean data data-refresh data-status dev mcp-serve docker-* info`. `ci-local = format-check lint-ci lint-loc typecheck-fast test-fast`.

## 3. Package layout

```
panelapp_link/
  __init__.py            # __version__ = "0.1.0"
  config.py              # ServerSettings + PanelAppDataConfigModel (pydantic-settings)
  constants.py           # confidence maps, ranks, region labels, citations
  exceptions.py          # PanelAppError hierarchy
  logging_config.py      # structlog config (copy sibling)
  server_manager.py      # UnifiedServerManager (stdio / http / unified)
  api/
    __init__.py
    client.py            # PanelAppRestClient: async httpx, semaphore, jittered backoff, DRF paging
  data/
    __init__.py
    schema.sql
    repository.py        # PanelAppRepository: read-only sqlite (file:...?mode=ro)
  models/
    __init__.py
    enums.py             # ResponseMode, Region, EntityType, ConfidenceLabel
    records.py           # PanelSummary, PanelDetail, Entity records, GenePanelHit, GeneSummary, BuildMeta
  services/
    __init__.py
    panelapp_service.py  # business logic + _TTLCache; returns plain dicts
    shaping.py           # response_mode shaping helpers
    refresh.py           # in-process conditional refresh scheduler
  ingest/
    __init__.py
    cli.py               # typer app: build | refresh | status  -> main()
    downloader.py        # async crawl of both regions (uses api/client)
    builder.py           # atomic SQLite build from crawled payloads
    lock.py              # cross-process build lock (copy sibling)
  mcp/
    __init__.py
    facade.py            # create_panelapp_mcp() -> FastMCP
    envelope.py          # run_mcp_tool, McpToolError, McpErrorContext, _classify, error envelopes
    schemas.py           # tool_output_schema() + per-tool JSON schemas
    capabilities.py      # build_capabilities(), register_capability_resources()
    next_commands.py     # cmd(), after_* builders, recovery_commands()
    resources.py         # static text resources
    annotations.py       # READ_ONLY_OPEN_WORLD
    middleware.py        # InputValidationMiddleware
    service_adapters.py  # get_panelapp_service() singleton + set_service_for_testing()
    tools/
      __init__.py
      _args.py           # region/confidence/gene coalescing + validation
      panels.py          # search_panels, get_panel, get_panel_genes
      genes.py           # get_gene_panels, resolve_gene
      discovery.py       # get_server_capabilities, get_panelapp_diagnostics
server.py                # argparse transport switch -> UnifiedServerManager
mcp_server.py            # stdio entry
```

## 4. PanelApp API facts (validated live 2026-06-16)

- Base URLs: UK `https://panelapp.genomicsengland.co.uk/api/v1`, AU `https://panelapp-aus.org/api/v1`. No auth.
- `/panels/?page=N` — DRF pagination (`count`, `next`, `results`). UK ~434 panels, AU ~261.
  - Panel (list) keys: `id, hash_id, name, disease_group, disease_sub_group, status, version, version_created, relevant_disorders[], types[]{name,...}, stats{number_of_genes,number_of_strs,number_of_regions}`. AU also: `description, child_panel_ids[]`.
- `/panels/signedoff/?page=N` — same shape **plus** `signed_off` (date string) + the `version` is the signed-off version. Merge into panel rows by `id`.
- `/panels/{id}/` — panel detail = panel fields **plus** `genes[]`, `regions[]`, `strs[]`.
- **Gene entity** keys: `entity_name, entity_type="gene", confidence_level (int|str), gene_data{gene_symbol, hgnc_id, gene_name, omim_gene, ensembl_genes, alias, biotype}, mode_of_inheritance, mode_of_pathogenicity, penetrance, phenotypes[], evidence[], publications[], tags[], transcript`.
- **Region entity** adds: `chromosome, grch37_coordinates, grch38_coordinates, haploinsufficiency_score, triplosensitivity_score, type_of_variants, verbose_name, required_overlap_percentage`; `gene_data` may be null.
- **STR entity** adds: `repeated_sequence, normal_repeats, pathogenic_repeats, chromosome, grch37_coordinates, grch38_coordinates`; `gene_data` present (associated gene).
- `confidence_level` arrives as int or string; **always cast to `str`**. Map: `"3","4"→green · "2"→amber · "1","0"→red`.

## 5. SQLite schema (`data/schema.sql`, schema_version = 1)

```
PRAGMA journal_mode = WAL; PRAGMA foreign_keys = OFF;

panel(
  region TEXT, panel_id INTEGER, hash_id TEXT,
  name TEXT NOT NULL, name_upper TEXT NOT NULL,
  version TEXT, version_created TEXT,
  disease_group TEXT, disease_sub_group TEXT, status TEXT,
  description TEXT,                      -- AU only, else NULL
  relevant_disorders_json TEXT DEFAULT '[]',
  types_json TEXT DEFAULT '[]',
  number_of_genes INTEGER DEFAULT 0, number_of_regions INTEGER DEFAULT 0, number_of_strs INTEGER DEFAULT 0,
  signed_off_version TEXT, signed_off_date TEXT,
  PRIMARY KEY (region, panel_id))
  idx panel(name_upper); idx panel(disease_group)

entity(
  region TEXT, panel_id INTEGER, entity_type TEXT,        -- gene|region|str
  entity_name TEXT,
  gene_symbol TEXT, gene_symbol_upper TEXT, hgnc_id TEXT,
  confidence_level TEXT, confidence_label TEXT,           -- green|amber|red
  confidence_rank INTEGER,                                -- green=3 amber=2 red=1 (filter ordering)
  mode_of_inheritance TEXT, penetrance TEXT,
  phenotypes_json TEXT DEFAULT '[]', evidence_json TEXT DEFAULT '[]',
  publications_json TEXT DEFAULT '[]', omim_json TEXT DEFAULT '[]', tags_json TEXT DEFAULT '[]',
  extra_json TEXT DEFAULT '{}',                           -- region/str-specific fields
  panel_name TEXT,                                        -- denormalized for gene->panels
  PRIMARY KEY (region, panel_id, entity_type, entity_name))
  idx entity(region, panel_id); idx entity(gene_symbol_upper); idx entity(hgnc_id)

gene(                                                     -- ingest-time roll-up across regions
  gene_symbol_upper TEXT PRIMARY KEY,
  gene_symbol TEXT, hgnc_id TEXT,
  panel_count INTEGER, regions_json TEXT DEFAULT '[]',
  max_confidence_label TEXT, max_confidence_rank INTEGER)
  idx gene(hgnc_id)

panel_fts USING fts5(region UNINDEXED, panel_id UNINDEXED, name, relevant_disorders, disease_group, tokenize='unicode61')

meta(id=1 single row: schema_version, source_uk_url, source_au_url,
     uk_panel_count, au_panel_count, entity_count, gene_count,
     build_utc, build_duration_s, panel_versions_json)         -- {region:{panel_id:version}} for incremental refresh
```

## 6. Models (`models/`)

- `enums.py`: `ResponseMode = Literal["minimal","compact","standard","full"]`; `Region = Literal["uk","australia","both"]`; `EntityType = Literal["gene","region","str","all"]`; `ConfidenceLabel = Literal["green","amber","red"]`. Tuples `RESPONSE_MODES`, `REGIONS`, etc.
- `records.py` (pydantic v2 `BaseModel`, `Field(description=...)`, `default_factory=list`):
  - `PanelSummary` (id, name, version, region, disease_group, disease_sub_group, status, relevant_disorders, n_genes/n_regions/n_strs, signed_off_version, signed_off_date).
  - `PanelDetail` (PanelSummary + entity count breakdown; entities attached by tool, not embedded).
  - `GeneEntity`, `RegionEntity`, `StrEntity` — or a single `Entity` with `entity_type` + typed `extra`. **Decision: single `Entity` model** with common fields + `extra: dict` for type-specific data (keeps models lean; matches JSON-blob schema).
  - `GenePanelHit` (region, panel_id, panel_name, version, confidence_label, confidence_level, mode_of_inheritance).
  - `GeneSummary` (gene_symbol, hgnc_id, panel_count, regions, max_confidence_label).
  - `BuildMeta` (provenance from `meta`).

## 7. Tool surface (`mcp/tools/`)

All tools: `region: Region = "both"`, `response_mode: ResponseMode = "compact"`, read-only, wrapped in `run_mcp_tool`. `min_confidence: ConfidenceLabel | None` filters by rank (`green` → only green; `amber` → amber+green; `red` → all). Cursor paging mirrors gencc (`truncated.next_cursor`, surfaced as `_meta.next_commands[0]`).

| Tool | Args | Returns |
|---|---|---|
| `search_panels` | `query="", region, response_mode, limit=20, cursor=None` | ranked `panels[]` (PanelSummary), `count`, `total`, `truncated` |
| `get_panel` | `panel_id:int, region` (uk\|australia — not both), `response_mode` | `panel` (PanelDetail) + entity count breakdown |
| `get_panel_genes` | `panel_id:int, region, entity_type="gene", min_confidence=None, response_mode, limit=100, cursor=None` | `entities[]`, counts, `truncated` |
| `get_gene_panels` | `gene_symbol=None, hgnc_id=None, region="both", min_confidence=None, response_mode` | `gene` identity + `panels[]` (GenePanelHit) across regions, grouped/sorted by confidence |
| `resolve_gene` | `query=None, gene_symbol=None, hgnc_id=None, response_mode` | resolved `gene` (GeneSummary) + `matches[]`; `ambiguous_query` if multiple |
| `get_server_capabilities` | — | inventory, vocab (confidence labels/ranks, entity types, regions), response_modes, workflows, error_codes, resources, `capabilities_version`, live `data` freshness |
| `get_panelapp_diagnostics` | — | build provenance/status from `meta` (data_unavailable if not built) |

**response_mode shaping:** `minimal` = ids+name+counts; `compact` (default) = key fields (panel: id/name/version/region/disease_group/counts/signed_off; entity: symbol/hgnc/confidence/moi); `standard` += phenotypes, penetrance, signed-off detail, region coords summary; `full` += evidence, publications, omim, tags, raw `extra`.

## 8. Envelope, errors, resources (copy sibling semantics)

- `run_mcp_tool(tool_name, call, *, context, response_mode)` injects `success`, `_meta.request_id`, `_meta.elapsed_ms`, provenance (citation_ref / citation_short / mode-aware), `unsafe_for_clinical_use=True`, and `_meta.next_commands`. Broad `except` → `_classify(exc)` → error envelope `{success:false, error_code, message, retryable, recovery_action, field_errors?, _meta}`.
- Exception hierarchy: `PanelAppError` → `InvalidInputError(field)`, `NotFoundError`, `AmbiguousQueryError(candidates)`, `DataUnavailableError`, `DownloadError(status_code)`, `RateLimitError(DownloadError)`. Plus `McpToolError(error_code, message)`.
- `_classify` map: McpToolError→its code; RateLimitError→`rate_limited`(retryable); DownloadError→`upstream_unavailable`(retryable); DataUnavailableError→`data_unavailable`; NotFoundError→`not_found`; AmbiguousQueryError→`ambiguous_query`; InvalidInputError/pydantic→`invalid_input`; else→`internal_error`.
- Error codes advertised: `invalid_input, not_found, ambiguous_query, data_unavailable, upstream_unavailable, rate_limited, internal_error`.
- Resources via `@mcp.resource`: `panelapp://capabilities` (json), `panelapp://usage`, `panelapp://reference`, `panelapp://license`, `panelapp://citation`, `panelapp://research-use` (text).
- Citations (verbatim): GE PanelApp — *Martin AR et al. PanelApp crowdsources expert knowledge... Nat Genet. 2019;51:1560-1565*; PanelApp Australia — *Stark Z et al., Australian Genomics PanelApp*. License note: PanelApp content under their terms; research use only; not clinical decision support.

## 9. API client (`api/client.py`)

`PanelAppRestClient(config, *, client=None)` — async httpx; `base_url` per region resolved by caller; `asyncio.Semaphore(max_concurrency)`; jittered exponential backoff (`0.5*2^attempt`, cap 8s) on `{429,500,502,503,504}`; `Accept: application/json`, custom `User-Agent`; injectable client for tests. Methods: `list_panels(base_url)` (async generator over DRF pages), `list_signed_off(base_url)`, `get_panel(base_url, panel_id)`. 403/429→`RateLimitError`, retryable 5xx/timeout→`DownloadError` after retries.

## 10. Ingest (`ingest/`)

- `cli.py` — typer app, commands `build` (force full crawl+rebuild), `refresh` (incremental: re-list, compare `panel_versions_json`, re-fetch changed/new only), `status` (print `meta`). `main()` exported for `panelapp-link-data`.
- `downloader.py` — async crawl both regions: list panels → fetch signed-off map → fetch each `/panels/{id}/` (bounded concurrency). Returns in-memory structures.
- `builder.py` — atomic build: write to `panelapp.sqlite.tmp`, `executescript(schema)`, insert panels/entities, roll up `gene`, build FTS, write `meta`, then atomic rename swap.
- `lock.py` — cross-process build lock (copy sibling).
- Auto-bootstrap on first use if `auto_bootstrap` and DB absent; in-process refresh scheduler in `services/refresh.py` for unified/http transports.

## 11. Config (`config.py`)

`ServerSettings(BaseSettings)` env_prefix `PANELAPP_LINK_`, nested `__`: host, port, transport(`unified|http|stdio`), mcp_path=`/mcp`, cors_*, log_level, log_format, `data: PanelAppDataConfigModel`. Data config: `uk_api_url`, `au_api_url`, `data_dir`, `db_filename="panelapp.sqlite"`, `request_timeout`, `max_concurrency`, `max_retries`, `user_agent`, `auto_bootstrap`, `refresh_enabled`, `refresh_interval_hours`, `refresh_jitter_seconds`, `build_lock_timeout`, `cache_size`, `cache_ttl`. `db_path` property.

## 12. Testing (TDD, respx, committed fixtures)

- `tests/fixtures/` — real captured payloads: `uk_panels_page1.json`, `uk_signedoff_page1.json`, `uk_panel_3.json`, `uk_panel_285.json` (has regions+strs), `au_panels_page1.json`, `au_panel_*.json`. Trim to a handful of panels for size.
- `conftest.py` — session fixture builds a temp SQLite from fixtures; `set_service_for_testing()` injects repo-backed service.
- Test files mirror siblings: `test_config, test_envelope, test_shaping, test_repository, test_service, test_tools, test_capabilities, test_next_commands, test_cli, test_ingest, test_api_client, test_data_lifecycle, test_tool_naming, test_app`.
- `make ci-local` green (format, lint, lint-loc, typecheck, tests) + coverage ≥85% + `docker build` succeeds. Integration tests (live API) marked `integration`, excluded from default run.

## 13. Definition of done

Tests green · ruff clean · mypy strict clean · `lint-loc` within budget · coverage ≥85% · `docker build` ok · `panelapp-link-data build` produces a working DB · `get_server_capabilities` + all `panelapp://` resources complete · README + AGENTS.md + CLAUDE.md + CHANGELOG + LICENSE + `.env*.example` + `docker/` + `.github/workflows/ci.yml` present and mirroring siblings.
```
