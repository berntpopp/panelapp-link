# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for PanelApp-Link.

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

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

# uv is pinned by digest (not a floating installer upgrade) so every build
# resolves the exact same installer artifact. Digest shared fleet-wide.
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --active --no-install-project


FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS production


ARG APP_VERSION=0.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="panelapp-link" \
      org.opencontainers.image.description="GeneFoundry MCP/API server for live PanelApp gene-panel research data" \
      org.opencontainers.image.url="https://github.com/berntpopp/panelapp-link" \
      org.opencontainers.image.source="https://github.com/berntpopp/panelapp-link" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry"
ENV VCS_REF="${VCS_REF}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    PANELAPP_LINK_HOST=0.0.0.0 \
    PANELAPP_LINK_PORT=8000 \
    PANELAPP_LINK_TRANSPORT=unified \
    PANELAPP_LINK_MCP_PATH=/mcp \
    TMPDIR=/tmp/panelapp-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/panelapp-link && \
    chown -R app:app /tmp/panelapp-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./panelapp_link ./panelapp_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN /opt/venv/bin/pip install . --no-deps && chmod +x /usr/local/bin/entrypoint.sh

USER app

EXPOSE 8000

# Stateless: PanelApp-Link is a pure live-API client (no database, no volume).
# It queries the PanelApp REST APIs per request with an in-memory cache, so the
# server is ready to serve immediately.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f -H 'Host: localhost' http://127.0.0.1:8000/health || exit 1

# entrypoint.sh is a thin passthrough that exec's this CMD.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Unified transport: FastAPI on / and MCP on /mcp via a single port.
CMD ["python", "server.py", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
