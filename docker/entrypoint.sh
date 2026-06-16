#!/bin/sh
# PanelApp-Link container entrypoint.
#
# PanelApp-Link is a pure live-API client: it has no database and no ingest
# step, so there is nothing to build or bootstrap here. This is a thin
# passthrough that hands PID 1 to the server command (CMD); it is kept as a
# seam for future pre-flight checks.
set -eu

exec "$@"
