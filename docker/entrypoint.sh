#!/bin/sh
# PanelApp-Link container entrypoint.
#
# Idempotent data bootstrap: build the PanelApp SQLite database once before the
# server starts, so the first request has predictable latency and never triggers
# a surprise crawl. `panelapp-link-data refresh` is safe to run on every start:
#   - missing database  -> crawls both regions (UK + Australia) and builds it
#   - existing database -> incremental refresh; re-lists panels, compares
#     versions, and re-fetches only changed/new panels (no full re-crawl)
# It is intentionally non-fatal: if the network is down but a database already
# exists in the mounted volume, we serve the existing data; if no database
# exists yet, the server still starts and tools report `data_unavailable` until
# the next refresh succeeds.
set -eu

echo "[entrypoint] ensuring PanelApp database is present and current..."
if panelapp-link-data refresh; then
    echo "[entrypoint] database ready."
else
    echo "[entrypoint] WARNING: initial refresh failed; starting with existing data if any." >&2
fi

# Hand off (PID 1) to the server command (CMD).
exec "$@"
