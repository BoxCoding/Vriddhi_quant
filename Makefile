.PHONY: help up down logs shell db-init db-migrate lint test clean dev

PYTHON := python3
PIP := pip3
COMPOSE := docker compose

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "NSE Options Trader — Available Commands"
	@echo "────────────────────────────────────────────────────────────────"
	@echo "  make up          Start all Docker services (DB + Redis)"
	@echo "  make down        Stop all Docker services"
	@echo "  make logs        Tail logs of all services"
	@echo "  make dev         Run the FastAPI server locally (not in Docker)"
	@echo "  make db-init     Initialize TimescaleDB schema"
	@echo "  make db-migrate  Run Alembic migrations"
	@echo "  make lint        Run ruff + mypy checks"
	@echo "  make test        Run pytest test suite"
	@echo "  make clean       Remove __pycache__ and .pyc files"
	@echo ""

# ── Docker ────────────────────────────────────────────────────────────────────
up:
	$(COMPOSE) up -d timescaledb redis
	@echo "✅  TimescaleDB and Redis are starting — run 'make db-init' once healthy"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

# ── Dev server ────────────────────────────────────────────────────────────────
dev:
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# ── Database ──────────────────────────────────────────────────────────────────
db-init:
	PGPASSWORD=$${POSTGRES_PASSWORD:-trader_secret} psql \
		-h localhost -U $${POSTGRES_USER:-trader} -d $${POSTGRES_DB:-nse_options} \
		-f db/schema.sql
	@echo "✅  Schema applied"

db-migrate:
	alembic upgrade head

# ── Code Quality ──────────────────────────────────────────────────────────────
lint:
	ruff check . --fix
	mypy . --ignore-missing-imports

fmt:
	black .

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --cov=. --cov-report=term-missing

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +

# ── Install ───────────────────────────────────────────────────────────────────
install:
	$(PIP) install -r requirements.txt

# ── Paper Trade ───────────────────────────────────────────────────────────────
paper:
	TRADING_MODE=paper $(PYTHON) -m agents.orchestrator.agent

# ── Live Trade (requires explicit confirmation) ───────────────────────────────
live:
	@echo "⚠️  WARNING: This will place REAL orders on NSE via Dhan!"
	@read -p "Type 'I UNDERSTAND' to continue: " ans; \
	if [ "$$ans" = "I UNDERSTAND" ]; then \
		TRADING_MODE=live $(PYTHON) -m agents.orchestrator.agent; \
	else \
		echo "Aborted."; \
	fi
