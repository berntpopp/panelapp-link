# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.3] - 2026-07-12

### Fixed

- Release the HTTP policy v1 remediation, including bounded retries, redirect
  handling, and upstream request safety controls. Research use only; not for
  clinical decision support.

## [0.5.2] - 2026-07-11

### Security (defense in depth)

- Guard FastMCP-core not-found reflection: a hostile unknown tool name, unknown/
  malformed resource URI, or unknown prompt name can no longer reflect the caller-
  supplied name/URI -- nor its control/zero-width/bidi/NUL code points -- into any
  caller-visible frame, any framework log record, or (uniquely for this backend,
  which ships an `otel` extra with `opentelemetry-sdk`) a recording OpenTelemetry
  span. Layer 1 registry preflight returns a fixed name-free envelope before core
  dispatch (so no OTel tool span is created); `on_read_resource` re-raises fixed
  URI-free errors; a protocol-handler backstop severs the `prompts/get` "Unknown
  prompt: '<name>'" caller echo (`panelapp_link/mcp/middleware.py`); a marker-based
  scrub filter neutralizes the FastMCP/MCP framework log records that reflect the
  name/URI at any level -- attached to each source logger, FastMCP's non-propagating
  Rich handlers, root, and `mcp.shared.session` (`panelapp_link/mcp/log_filters.py`);
  and a span-exception redactor scrubs a recording span's name, caller-controlled
  attributes, exception events, and status
  (`panelapp_link/observability/tracing.py`). All error strings are fixed constants.
  Research use only.

## [0.5.1] - 2026-07-11

### Security (defense in depth)

- Caller-visible error messages are sanitized and severed of upstream/transport/
  exception detail and control/zero-width/bidi/NUL code points; argument-
  validation, unknown-tool, and unknown-resource paths return fixed input-free
  errors that never echo the caller-supplied name/URI. Research use only.

## [0.5.0] - 2026-07-11

### BREAKING

- **Curator prose is now fenced as a typed `untrusted_text` object (Response-
  Envelope Standard v1.1), never a bare string.** These MCP surfaces now emit
  `{kind: "untrusted_text", text, provenance: {source, record_id, retrieved_at},
  raw_sha256}` in place of the raw string:
  - `get_panel` `/panel/description` + `/panel/types/*/description`,
  - `search_panels` `/panels/*/description` + `/panels/*/types/*/description`
    (both panel tools route through the shared `shape_panel` boundary),
  - `get_panel_genes` `/entities/*/phenotypes` + `/entities/*/evidence`
    (`shape_entity`; each list element fenced as its own object).

  `text` is NFC-normalized with control/zero-width/bidi code points removed
  (`panelapp_link/mcp/untrusted_content.py`, copied verbatim from the
  fleet-reference `pubtator-link` fence); `raw_sha256` is the digest of the
  pre-normalization bytes. `record_id` is **region-qualified** because PanelApp
  panel ids are per-region: `panel:{region}:{id}` for the panel description,
  `panel:{region}:{id}#type:{slug}` for a panel type, and
  `panel:{region}:{id}#gene:{symbol}` (falling back to `#entity:{name}` for
  region/str entities) for phenotypes/evidence. Standard/full response modes
  only; minimal/compact are unaffected. `enforce_untrusted_text_limits` guards
  every response over the whole payload (`get_panel`: default 128 for the single
  record; `search_panels` + `get_panel_genes`: a generous 10000-object ceiling,
  since a page of up to 500 records each carrying several fenced prose fields can
  legitimately exceed the bare 128 default — the 2 MiB/object + 8 MiB/total byte
  limits remain the DoS floor). A limit breach surfaces as an explicit typed
  `limit_exceeded` error code (never a generic `internal_error`), advertised in
  `get_server_capabilities`. This is defense in depth: the router already treats
  a `kind: untrusted_text` subtree opaque; fencing types upstream prose as data
  at the source. Research use only; not clinical decision support.

## [0.4.0] - 2026-07-11

### Security

- **Re-enable FastMCP 3.4.4 strict Host/Origin protection (default-deny).** The
  MCP Streamable-HTTP transport now enforces strict `Host`/`Origin` validation
  again on FastMCP 3.4.4, with configurable `ALLOWED_HOSTS`/`ALLOWED_ORIGINS`
  allowlists that default to deny. The previous version-safe guard that
  pre-empted the FastMCP 3.4.3 host-origin 421 is superseded by first-class
  allowlist configuration. **Deploy prerequisite:** the proxied public host must
  be present in the allowlist or the router federation will receive HTTP 421 —
  see `strato_v6_docker_npm#3`. New guard tests lock the behaviour
  (`tests/test_host_origin_guard.py`).

## [0.3.3] - 2026-07-07

### Security

- **Harden the inbound trust boundary of this unauthenticated backend.** CORS
  credentials are now **off by default** (`cors_allow_credentials=False`): the
  backend holds no cookies/session, so credentialed CORS is meaningless, and the
  app now **fails closed at startup** if credentials are ever enabled together
  with a `*` origin. The base `docker/docker-compose.yml` now **loopback-binds**
  the published host port (`127.0.0.1:…`) so copying it to a server never
  publishes the backend on the public IP past the host firewall; production
  still fronts the container via the router / reverse-proxy overlays. New guard
  tests lock both behaviours (`tests/test_cors.py`,
  `tests/test_docker_compose_loopback.py`); the CORS guard asserts the verb list
  wired into the installed middleware, not just the settings object.

## [0.3.2] - 2026-07-03

### Fixed

- **MCP `serverInfo.version` now advertises the package version, not the FastMCP
  framework version.** The `FastMCP(...)` constructor in the MCP facade was
  created without a `version=` argument, so the `initialize` response reported the
  installed FastMCP framework version (e.g. `3.4.2`) instead of the package
  version. The facade now passes `version=__version__`, single-sourcing the MCP
  server version from package metadata in lockstep with `/health` and structured
  logs. A guard test (`tests/unit/test_version_single_source.py`) locks
  pyproject → installed metadata → `__version__` → MCP `serverInfo.version` to one
  value.

## [0.3.1] - 2026-06-29

### Security

- Adopt the **GeneFoundry Container & Deployment Hardening Standard v1**
  (closes #4): pin the base image by digest
  (`python:3.12-slim@sha256:423ed6a…`), add a CI container scan + SBOM workflow,
  and never send CORS credentials with a wildcard origin.

## [0.3.0] - Unreleased

### Added

- **`compare_panels`** — diff the genes of **2–5** panels server-side. Returns
  `shared`, `only_in` (genes unique per `panel_id@region`), `confidence_deltas`
  (per-panel label for differing genes), and a `summary` (`n_shared`, `n_union`).
  Each ref needs a concrete region (`uk`/`australia`); `both` is rejected.
- **`get_panels_for_genes`** — batch gene→panel membership for up to 20 symbols in
  one call (per gene: `panel_count`, `max_confidence_label`, panels). Unknown
  symbols collect in `not_found`; over-cap input is truncated. The cap is
  configurable via `PANELAPP_LINK_DATA__GENE_BATCH_CAP` (default 20). Fan-out
  rides the shared cache + concurrency semaphore for upstream politeness.
- **`confidence_counts`** on panel detail (`get_panel` in `standard`/`full`) — a
  per-entity-type traffic-light tally, e.g. `{"gene": {"green": N, "amber": N,
  "red": N}}`. Additive: `entity_counts` is unchanged (still integer totals).
- **Opt-in OpenTelemetry OTLP tracing.** `setup_tracing()` installs an OTLP
  `TracerProvider` on startup when `PANELAPP_LINK_OTEL__ENABLED=true` and the new
  `otel` extra is installed (`pip install 'panelapp-link[otel]'`). No-op
  otherwise; the optional console exporter is stderr-only and suppressed under the
  stdio transport so it can never corrupt the MCP JSON-RPC channel.
- The hosted surface is now **9 read-only tools** (advertised in capabilities and
  `panelapp://usage`).

### Changed

- **Leaner `minimal` mode `_meta`** for sweep / agent-loop workloads: drops the
  per-region `upstream` / `upstream_ms` timing and the redundant `citation_short`
  (the cacheable `citation_ref` stub still rides), and trims `next_commands` to the
  single highest-value step.
- **Trimmed per-tool descriptions** (`search_panels`, `get_panel`,
  `get_panel_genes`, `get_gene_panels`) to cut the per-request token tax; the full
  workflow guidance lives in `get_server_capabilities` and `panelapp://usage`.
- `resolve_gene` prose now names the real `max_confidence_label` field instead of
  the prose "strongest confidence".

### Fixed

- **M1 — full-mode panel count fields.** `full` mode leaked the upstream
  `number_of_genes` / `number_of_regions` / `number_of_strs` names while every
  other mode used `n_genes` / `n_regions` / `n_strs`, so consumers broke when
  widening verbosity. The shaper now emits `n_*` in **all** modes; the shared
  count fields are mode-invariant.

## [0.2.0] - Unreleased

### Changed

- **Switched to a pure live-API backend.** PanelApp-Link now queries the public
  Genomics England (UK) and PanelApp Australia REST APIs **per request** and
  memoizes raw payloads in an in-memory TTL cache (default 6h,
  `PANELAPP_LINK_DATA__CACHE_TTL=21600`). The server is stateless: no local
  database, no data directory, no volume, and no build step. Politeness is tuned
  for live use — low concurrency (default 4), jittered backoff, and `Retry-After`
  handling for PanelApp's HTTP 429 throttling.
- **Tool-to-API mapping.** `get_gene_panels` / `resolve_gene` →
  `GET /genes/?entity_name=SYMBOL` (one call per region); `get_panel` /
  `get_panel_genes` → `GET /panels/{id}/`; `search_panels` fetches the cached
  panel list and filters in memory; signed-off status comes from the cached
  `GET /panels/signedoff/`. The 7-tool surface and arguments are unchanged.
- **Diagnostics & capabilities** now report the live sources and cache state
  (TTL, entries) instead of build provenance and per-region freshness timestamps.

### Removed

- **The SQLite mirror, the `ingest/` crawler, the `data/` store (schema + read-only
  repository), and the in-app refresh scheduler.** Data is no longer mirrored
  locally.
- **The `panelapp-link-data` CLI** (`build` / `refresh` / `status`) and its
  `make data*` targets — there is no build step to run.
- **Data/store/refresh config:** `data_dir`, `db_filename`, `auto_bootstrap`,
  `refresh_enabled`, `refresh_interval_hours`, `refresh_jitter_seconds`,
  `build_lock_timeout`. The `data_unavailable` error code is gone (no database to
  be unavailable).

## [0.1.0] - 2026-06-16

Initial release. PanelApp-Link is a read-only MCP + FastAPI server that mirrors
**both** PanelApp instances — Genomics England PanelApp (UK) and PanelApp
Australia — into a local SQLite database and answers panel/gene questions over
either or both regions. A drop-in sibling of the `*-link` MCP fleet.

### Added

- **Both-region PanelApp mirror.** An async ingest pipeline crawls the public UK
  and Australia PanelApp REST APIs (`/panels/`, `/panels/signedoff/`,
  `/panels/{id}/`; no auth), merges the signed-off version + date into each
  panel, and builds a read-only SQLite + FTS5 store. The MCP tools query SQLite
  only — no live API calls at request time.
- **All three entity types.** Genes, regions (CNVs), and STRs (short tandem
  repeats) are ingested and queryable, each carrying its traffic-light
  confidence (green / amber / red), mode of inheritance, phenotypes, and
  type-specific fields.
- **Cross-region gene roll-up.** A `gene` table aggregates each gene across both
  regions (panel count, regions present, max confidence) for fast
  gene-to-panels lookups.
- **7 MCP tools** with token-efficient `response_mode` shaping
  (`minimal` | `compact` | `standard` | `full`), typed `outputSchema`,
  plain-English headlines, and ready-to-call `_meta.next_commands` chains on
  success **and** error envelopes:
  - `search_panels`, `get_panel`, `get_panel_genes`
  - `get_gene_panels`, `resolve_gene`
  - `get_server_capabilities`, `get_panelapp_diagnostics`
- **`region` argument** (`uk` | `australia` | `both`, default `both`) on the data
  tools, and `min_confidence` filtering by traffic-light rank
  (green = only green; amber = amber + green; red = all).
- **Confidence normalization.** `confidence_level` (int or string from upstream)
  is cast to `str` and mapped to a `confidence_label` and `confidence_rank` at
  ingest time, so filtering and ordering are stable.
- **Ingest CLI** (`panelapp-link-data`): `build` (force full crawl + rebuild),
  `refresh` (incremental — re-list, compare panel versions, re-fetch only
  changed/new panels), `status` (print build provenance).
- **Data lifecycle.** Idempotent build-on-startup via the container entrypoint, an
  optional in-app conditional-refresh scheduler (unified/http transports), a
  cross-process build lock, and atomic database swaps.
- **Three transports** from one codebase: `unified` (REST + MCP on one port),
  `http`, and `stdio` (for Claude Desktop and similar local clients).
- **Agent-discoverable resources:** `panelapp://capabilities` (JSON),
  `panelapp://usage`, `panelapp://reference`, `panelapp://license`,
  `panelapp://citation`, and `panelapp://research-use`.
- **Typed error envelopes:** `invalid_input`, `not_found`, `ambiguous_query`,
  `data_unavailable`, `upstream_unavailable`, `rate_limited`, `internal_error`,
  each with `retryable`, a `recovery_action`, and recovery `next_commands`.
- **Observability:** every `_meta` carries a `request_id` and server-side
  `elapsed_ms`; `get_panelapp_diagnostics` reports build provenance, per-region
  panel counts, and data freshness.
- **Packaging:** multi-stage Docker image, dev + production Compose files, CI and
  release GitHub Actions workflows, README, AGENTS.md / CLAUDE.md, architecture /
  usage / data-lifecycle docs, and a Claude Desktop config sample.

### Data sources & license

- **Code:** MIT.
- **Data:** Genomics England PanelApp and PanelApp Australia content under their
  respective terms. Research use only; not clinical decision support.
