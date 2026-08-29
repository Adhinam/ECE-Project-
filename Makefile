# Store Intelligence — development & Docker commands
# Windows: use Git Bash or WSL for best compatibility; PowerShell works for most targets.

.PHONY: help env install install-dev lint format test test-unit test-integration \
        migrate migrate-down \
        up up-detach up-dev up-dev-detach down down-v ps logs logs-api logs-db health \
        build rebuild shell-api shell-db \
        dev-api dev-dashboard dev-install

COMPOSE ?= docker compose
COMPOSE_DEV := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
ENV_FILE := .env

help:
	@echo "Store Intelligence — Make targets"
	@echo ""
	@echo "Docker (production images):"
	@echo "  make env          Create .env from .env.example if missing"
	@echo "  make up           Build and start stack (foreground)"
	@echo "  make up-detach    Build and start stack (background)"
	@echo "  make down         Stop stack (keep DB volume)"
	@echo "  make down-v       Stop stack and remove volumes"
	@echo "  make build        Build images only"
	@echo "  make logs         Follow all service logs"
	@echo "  make health       Hit API /health"
	@echo ""
	@echo "Docker (dev hot-reload):"
	@echo "  make up-dev       Compose + dev bind mounts"
	@echo "  make up-dev-detach"
	@echo ""
	@echo "Local Python:"
	@echo "  make dev-install  pip install requirements"
	@echo "  make dev-api      Run API on :8000 (needs local Postgres)"
	@echo "  make dev-dashboard Run dashboard on :8501"
	@echo "  make migrate      Alembic upgrade (local)"
	@echo "  make test         pytest"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

env:
	@python -c "import pathlib, shutil; p=pathlib.Path('$(ENV_FILE)'); shutil.copy('.env.example', p) if not p.exists() else print('$(ENV_FILE) already exists')"

# ---------------------------------------------------------------------------
# Python local dev
# ---------------------------------------------------------------------------

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-dev.txt

dev-install: install-dev

dev-api: env
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-dashboard: env
	streamlit run dashboard/streamlit_app.py --server.port 8501

lint:
	ruff check schemas api app pipeline ingest dashboard tests
	mypy schemas api app pipeline ingest --ignore-missing-imports

format:
	ruff format schemas api app pipeline ingest dashboard tests

test:
	pytest

test-unit:
	pytest -m "unit or not integration" --ignore=tests/integration

test-integration:
	pytest -m integration tests/integration

migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

# ---------------------------------------------------------------------------
# Docker Compose — production stack
# ---------------------------------------------------------------------------

build:
	$(COMPOSE) build

rebuild:
	$(COMPOSE) build --no-cache

up: env build
	$(COMPOSE) up

up-detach: env build
	$(COMPOSE) up -d
	@echo "API:       http://localhost:$${API_PUBLISH_PORT:-8000}/docs"
	@echo "Dashboard: http://localhost:$${DASHBOARD_PUBLISH_PORT:-8501}"
	@echo "Health:    http://localhost:$${API_PUBLISH_PORT:-8000}/health"

up-dev: env
	$(COMPOSE_DEV) up --build

up-dev-detach: env
	$(COMPOSE_DEV) up --build -d

down:
	$(COMPOSE) down

down-v:
	$(COMPOSE) down -v

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs -f

logs-api:
	$(COMPOSE) logs -f api

logs-db:
	$(COMPOSE) logs -f postgres

health:
	@curl -fsS "http://localhost:$${API_PUBLISH_PORT:-8000}/health" | python -m json.tool || \
		curl -fsS "http://localhost:8000/health"

shell-api:
	$(COMPOSE) exec api sh

shell-db:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-store_intel} -d $${POSTGRES_DB:-store_intel}

# ---------------------------------------------------------------------------
# Data utilities
# ---------------------------------------------------------------------------

replay:
	python -m ingest.replay --input data/events --api-url $${INGEST_API_URL:-http://localhost:8000}

seed:
	python scripts/seed_demo_data.py

# Full CV pipeline profile
up-full: env
	$(COMPOSE) --profile full up --build
