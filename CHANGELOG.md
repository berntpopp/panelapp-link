# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
