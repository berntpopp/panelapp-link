.PHONY: help install lock upgrade sync \
        format format-check lint lint-ci lint-fix lint-loc lint-readme \
        typecheck typecheck-fast test test-fast test-unit test-integration test-cov \
        check ci-local precommit clean \
        data data-refresh data-status dev mcp-serve \
        docker-build docker-up docker-down docker-logs docker-prod-config info

DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

.DEFAULT_GOAL := help

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format panelapp_link tests server.py mcp_server.py scripts

format-check: ## Check formatting without writing
	uv run ruff format --check panelapp_link tests server.py mcp_server.py scripts

lint: ## Lint Python code
	uv run ruff check panelapp_link tests server.py mcp_server.py scripts

lint-ci: ## Lint with GitHub-Actions output
	uv run ruff check panelapp_link tests server.py mcp_server.py scripts --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check panelapp_link tests server.py mcp_server.py scripts --fix

lint-loc: ## Enforce per-file line budget (see AGENTS.md "File Size Discipline")
	uv run python scripts/check_file_size.py

lint-readme: ## Enforce the GeneFoundry README Standard v1
	uv run python scripts/check_readme.py

typecheck: ## Type check package
	uv run mypy panelapp_link server.py mcp_server.py

typecheck-fast: ## Type check with mypy daemon and fallback
	@tmp_log=$$(mktemp); \
	if uv run dmypy run -- panelapp_link server.py mcp_server.py >$$tmp_log 2>&1; then \
		cat $$tmp_log; \
	else \
		echo "dmypy unavailable/failed; falling back to plain mypy..."; \
		uv run dmypy stop >/dev/null 2>&1 || true; \
		uv run mypy panelapp_link server.py mcp_server.py; \
	fi; \
	rm -f $$tmp_log

test: ## Run tests quickly
	uv run pytest tests -q -m "not integration"

test-fast: ## Run tests in parallel with pytest-xdist
	uv run pytest tests -q -n auto -m "not integration"

test-unit: ## Run unit tests in parallel
	uv run pytest tests -q -n auto -m "not integration and not slow"

test-integration: ## Run integration tests (live PanelApp API) serially
	uv run pytest tests -q -m "integration"

test-cov: ## Run tests with coverage
	uv run pytest tests -m "not integration" --cov=panelapp_link --cov-report=term-missing --cov-report=html

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc lint-readme typecheck-fast test-fast ## Fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml dist build

data: ## Crawl PanelApp (UK + AU) and build the SQLite database
	uv run panelapp-link-data build

data-refresh: ## Refresh the database incrementally (only changed/new panels)
	uv run panelapp-link-data refresh

data-status: ## Show build provenance/status for the local database
	uv run panelapp-link-data status

dev: ## Start unified REST + MCP development server
	uv run python server.py --transport unified --host 127.0.0.1 --port 8000

mcp-serve: ## Start local stdio MCP server
	uv run python mcp_server.py

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f

docker-prod-config: ## Render production Compose configuration
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml config

info: ## Show project information
	@echo "Project: PanelApp-Link"
	@echo "uv: $(shell uv --version 2>/dev/null || echo 'not installed')"
