# panelapp-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/panelapp-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/panelapp-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/panelapp-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/panelapp-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An MCP server (Streamable HTTP) that grounds **gene-panel** questions in
**PanelApp** — the crowdsourced, expert-reviewed catalogue of consensus
diagnostic gene panels — across **both** instances: Genomics England PanelApp
(UK) and PanelApp Australia.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

PanelApp's public REST API is a per-region, panel-shaped CRUD surface, and three
of its properties make it awkward to ask the questions clinicians and curators
actually ask:

- **It is two APIs, not one.** UK and Australia are separate deployments with no
  cross-region view, so "which panels carry this gene?" has to be asked twice and
  reconciled by hand.
- **There is no server-side panel search.** A client must page the whole
  `/panels/` listing and filter it itself — and PanelApp answers aggressive
  per-IP bursts with HTTP 429.
- **Signed-off metadata lives elsewhere.** A panel's signed-off version and date
  come from a different endpoint than the panel it describes.

panelapp-link pays that fan-out once, behind one tool call: it queries both
regions live, filters panels in memory, merges the signed-off metadata, and rolls
genes up across regions (panel count, regions present, max confidence). It then
adds two aggregations upstream has no endpoint for — a gene-level diff of 2-5
panels, and panel membership for a batch of gene symbols in a single call.

## Quick start

The server is hosted — no install, and **no API key** (both upstream APIs are
public and unauthenticated):

```bash
claude mcp add --transport http panelapp-link https://panelapp-link.genefoundry.org/mcp
```

To run it locally (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
uv sync
make dev                                    # unified REST + MCP on 127.0.0.1:8000
claude mcp add --transport http panelapp-link http://127.0.0.1:8000/mcp
curl -s localhost:8000/health
```

**There is no data build step.** panelapp-link is a pure live-API client: no
database, no ingest, no volume. It queries the PanelApp REST APIs per request and
memoizes raw payloads in an in-memory TTL cache (6 hours by default), so it is
ready to serve as soon as it starts.

`make mcp-serve` runs the stdio server instead, for Claude Desktop; the config
block is in [Deployment](docs/deployment.md#connecting-an-mcp-client).

## Tools

| Tool | Purpose |
|------|---------|
| `search_panels` | Find panels by name, disorder, or disease group, across regions |
| `get_panel` | One panel's detail and entity-count breakdown |
| `get_panel_genes` | A panel's entities — gene, region (CNV), or STR — filterable by confidence |
| `get_gene_panels` | Every panel a gene appears on, rolled up across regions |
| `get_panels_for_genes` | Panel membership for a batch of gene symbols in one call |
| `compare_panels` | Diff the genes of 2-5 panels: shared, only-in, confidence deltas |
| `resolve_gene` | Resolve a symbol, HGNC id, or free text to a gene (with `matches[]` if ambiguous) |
| `get_server_capabilities` | Tool inventory, vocabularies, response modes, live sources |
| `get_panelapp_diagnostics` | Live upstream sources, cache TTL, and cache stats |

The server identity is `panelapp-link` (`serverInfo.name`). Leaf names are
unprefixed per Tool-Naming Standard v1; behind
[genefoundry-router](https://github.com/berntpopp/genefoundry-router) they surface
namespaced under the `panelapp` token as `panelapp_<tool>` — e.g.
`panelapp_search_panels`.

Data tools take `region` (`uk` | `australia` | `both`, default `both`) and
`response_mode` (`minimal` | `compact` | `standard` | `full`, default `compact`);
entity tools take `min_confidence`, which filters by traffic-light rank (green =
only green; amber = amber + green; red = all). Workflows and the citation
contract: [Usage](docs/usage.md).

## Data & provenance

**Sources** — two public, no-auth REST APIs, queried live:

- Genomics England PanelApp (UK) — [`panelapp.genomicsengland.co.uk/api/v1`](https://panelapp.genomicsengland.co.uk/api/v1)
- PanelApp Australia — [`panelapp-aus.org/api/v1`](https://panelapp-aus.org/api/v1)

**Refresh model** — no snapshot and no release cadence: every query hits upstream
and is memoized in an in-memory TTL cache, so results are always the current
upstream data, bounded only by the TTL. Each panel keeps its **latest** version
alongside the `signed_off_version` / `signed_off_date` merged from the signed-off
endpoint. See [Data freshness & caching](docs/data-lifecycle.md).

**Data licence** — PanelApp content is provided by Genomics England and by
Australian Genomics under their **respective** terms; there is no single blanket
licence. Consult each portal for attribution and reuse terms. PanelApp data is
not intended for direct diagnostic use or medical decision-making without review
by a genetics professional.

**Citation** — cite the panels you used:

> **Genomics England PanelApp** — Martin AR, Williams E, Foulger RE, et al.
> PanelApp crowdsources expert knowledge to establish consensus diagnostic gene
> panels. Nat Genet. 2019;51:1560-1565.
>
> **PanelApp Australia** — Australian Genomics PanelApp
> ([panelapp-aus.org](https://panelapp-aus.org)).

## Documentation

- [Usage](docs/usage.md) — canonical workflows, common arguments, `response_mode` guidance, the citation contract, and the `panelapp://` resources.
- [Architecture](docs/architecture.md) — the live-API + cache design, layers, per-tool API mapping, error taxonomy, and observability.
- [Data freshness & caching](docs/data-lifecycle.md) — what is cached, and how the client stays polite to a rate-limiting upstream.
- [Configuration](docs/configuration.md) — every `PANELAPP_LINK_*` variable, plus the Host / Origin / CORS boundary.
- [Deployment](docs/deployment.md) — connecting MCP clients, the Docker stacks, and the hardened production overlay.
- [Changelog](CHANGELOG.md) — released versions.

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions, the per-file line
budget, and the testing layout. `make ci-local` is the definition-of-done gate:
format, lint, line budget, README standard, mypy, and tests.

## License

[MIT](LICENSE) © Bernt Popp — code only. **PanelApp data** remains under the
terms of Genomics England PanelApp and of PanelApp Australia respectively; see
[Data & provenance](#data--provenance).
