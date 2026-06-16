# Data lifecycle

PanelApp exposes public, no-auth REST APIs for two regions (Genomics England
PanelApp UK and PanelApp Australia), but a full mirror means many `/panels/{id}/`
detail requests. PanelApp-Link therefore builds a local **SQLite + FTS5** artifact
and keeps it current with a two-part lifecycle:

1. **Build once on startup** — before the server accepts traffic, so the first
   request has predictable latency and never triggers a surprise crawl.
2. **Refresh on a schedule** — an *incremental* refresh that re-lists panels,
   compares them to the stored per-panel versions, re-fetches only the changed or
   new panels, then **hot-reloads** the running server.

```
            ┌──────────────────────────── container / pod ────────────────────────────┐
 startup ──▶│ entrypoint: panelapp-link-data refresh  (build if missing, else          │
            │        │ incremental: only changed/new panels)                            │
            │        │ writes data/panelapp.sqlite (atomic os.replace under a file lock) │
            │        ▼                                                                  │
            │   server (unified)  ──reads──▶ data/panelapp.sqlite (read-only conn.)     │
            │        ▲                                                                  │
            │   in-app scheduler (every 24h): incremental refresh ──changed?──▶ rebuild │
            │        └────────── on change: reset cached service ──▶ reopen (hot-reload)│
            └───────────────────────────────────────────────────────────────────────────┘
```

## Building blocks

- **`panelapp-link-data` CLI** (`panelapp_link/ingest/cli.py`) — the unit of work
  any scheduler calls:
  - `build` — force a full crawl of both regions + rebuild.
  - `refresh` — incremental; re-lists panels, compares to the stored
    `panel_versions_json`, and re-fetches only changed/new panels (a full
    no-change refresh re-crawls nothing beyond the cheap panel listings).
  - `status` — print provenance of the existing database.
- **Build lock** (`panelapp_link/ingest/lock.py`) — a cross-process file lock on
  the data directory serializes every builder (entrypoint, in-app scheduler,
  sidecar, CronJob) so they never crawl or rebuild concurrently.
- **Atomic swap** — the builder writes `panelapp.sqlite.tmp` and `os.replace`s it
  into place, so readers always see a complete database.
- **Hot reload** — when the database file's mtime changes (any builder swapped
  it), the read-only connection / cached service is reset and reopened. This is
  what lets an *external* scheduler refresh the data and have the running server
  pick it up with no restart.
- **In-app scheduler** (`panelapp_link/services/refresh.py`) — a dependency-free
  asyncio loop started from the FastAPI lifespan (unified/http only; never stdio).
  First run is one interval after startup; the blocking crawl + build runs in a
  worker thread. Surfaced in `get_panelapp_diagnostics`.

## Incremental refresh semantics

`build` always re-fetches every `/panels/{id}/` for both regions. `refresh` is
cheaper: it re-lists `/panels/` (and `/panels/signedoff/`) for each region — a
handful of paged requests — and compares each panel's reported `version` to the
stored `panel_versions_json`. Only panels whose version changed (or that are new)
have their detail re-fetched; unchanged panels reuse the stored rows. A refresh
where nothing changed therefore costs only the panel listings and rebuilds
nothing. PanelApp panels change incrementally, so a daily refresh stays light.

## Choosing a refresh strategy

Pick **one** owner for the periodic refresh. All options share the build lock and
the hot-reload, so they are safe to mix only if exactly one is enabled.

| Strategy | When | How |
|----------|------|-----|
| **In-app scheduler** (default) | Single container / single deployment | `PANELAPP_LINK_DATA__REFRESH_ENABLED=true` (default). Nothing else to run. |
| **Host cron / systemd timer** | Bare VM | Disable the in-app loop and schedule `panelapp-link-data refresh`. |
| **k8s CronJob** | Kubernetes, external scheduler | Set `REFRESH_ENABLED=false` on the Deployment and run a CronJob that execs `panelapp-link-data refresh`; needs a shared (ReadWriteMany) volume. |

### Host cron example

```cron
# Daily incremental refresh (PanelApp panels change incrementally).
17 3 * * *  cd /opt/panelapp-link && /opt/panelapp-link/.venv/bin/panelapp-link-data refresh >> /var/log/panelapp-refresh.log 2>&1
```

### systemd timer example

```ini
# /etc/systemd/system/panelapp-refresh.service
[Service]
Type=oneshot
WorkingDirectory=/opt/panelapp-link
ExecStart=/opt/panelapp-link/.venv/bin/panelapp-link-data refresh
Environment=PANELAPP_LINK_DATA__DATA_DIR=/var/lib/panelapp-link

# /etc/systemd/system/panelapp-refresh.timer
[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true
[Install]
WantedBy=timers.target
```

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `PANELAPP_LINK_DATA__AUTO_BOOTSTRAP` | `true` (image: `false`) | Build lazily on first use if absent. The image sets `false` because the entrypoint builds on startup. |
| `PANELAPP_LINK_DATA__REFRESH_ENABLED` | `true` | Run the in-app scheduler (unified/http only). Set `false` when an external scheduler owns refresh. |
| `PANELAPP_LINK_DATA__REFRESH_INTERVAL_HOURS` | `24` | Hours between incremental refresh checks. |
| `PANELAPP_LINK_DATA__REFRESH_JITTER_SECONDS` | `300` | Random jitter added per cycle. |
| `PANELAPP_LINK_DATA__BUILD_LOCK_TIMEOUT` | `600` | Seconds to wait for the build lock before giving up. |
| `PANELAPP_LINK_DATA__MAX_CONCURRENCY` | `8` | Max concurrent API requests during a crawl. |
| `PANELAPP_LINK_DATA__MAX_RETRIES` | `4` | Retries on 429/5xx/timeout (jittered backoff). |

## Politeness to upstream

PanelApp-Link bounds crawl concurrency with a semaphore, sends a descriptive
`User-Agent`, and retries retryable responses (429/5xx/timeout) with jittered
exponential backoff. The incremental `refresh` re-fetches only changed/new panels,
so steady-state load on the PanelApp APIs is minimal — a few panel-listing
requests per region per day.

## Observability

`get_panelapp_diagnostics` returns build provenance (source URLs, per-region panel
counts, entity / gene counts, build timestamp) plus the refresh scheduler state
(enabled, interval, whether it is running, last check, and any last error).
Structured logs record each scheduler decision and any crawl/quota failure.
