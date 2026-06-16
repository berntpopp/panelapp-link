# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for PanelApp-Link.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /home/app/web
COPY uv.lock pyproject.toml README.md ./

RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev --active --no-install-project


FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    PANELAPP_LINK_HOST=0.0.0.0 \
    PANELAPP_LINK_PORT=8000 \
    PANELAPP_LINK_TRANSPORT=unified \
    PANELAPP_LINK_MCP_PATH=/mcp \
    PANELAPP_LINK_DATA__DATA_DIR=/app/data \
    # The entrypoint builds the database before the server starts, so the
    # request path never triggers a lazy build. The in-app scheduler then keeps
    # it fresh; disable it and use the cron sidecar / k8s CronJob if you prefer
    # an external scheduler (see docs/data-lifecycle.md).
    PANELAPP_LINK_DATA__AUTO_BOOTSTRAP=false \
    PANELAPP_LINK_DATA__REFRESH_ENABLED=true \
    PANELAPP_LINK_DATA__REFRESH_INTERVAL_HOURS=24 \
    TMPDIR=/tmp/panelapp-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/panelapp-link /app/data && \
    chown -R app:app /tmp/panelapp-link /app/data /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./panelapp_link ./panelapp_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN /opt/venv/bin/pip install . --no-deps && chmod +x /usr/local/bin/entrypoint.sh

USER app

EXPOSE 8000

# The built SQLite database lives under PANELAPP_LINK_DATA__DATA_DIR (/app/data).
# Mount a volume there so the database persists across container restarts; the
# entrypoint crawls both PanelApp regions and builds it once on startup, then the
# in-app scheduler refreshes it.
VOLUME ["/app/data"]

# start-period covers the entrypoint's first build (crawl of both regions +
# build) before the server begins serving; the server itself starts in well
# under a second.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# entrypoint.sh builds/refreshes the database, then exec's this CMD.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Unified transport: FastAPI on / and MCP on /mcp via a single port.
CMD ["python", "server.py", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
