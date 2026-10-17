SHELL := /bin/bash
PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip
UVICORN := backend/.venv/bin/uvicorn
PYTEST := backend/.venv/bin/pytest
RUFF := backend/.venv/bin/ruff
NPM := npm --prefix frontend

.DEFAULT_GOAL := help
.PHONY: help install install-backend install-frontend test test-backend test-frontend \
        test-integration lint lint-backend lint-frontend format security security-selftest \
        dev dev-backend dev-frontend up down logs bench clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install: install-backend install-frontend ## Install backend and frontend dependencies

install-backend: ## Create backend/.venv and install dependencies
	python3 -m venv backend/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "backend[dev]"
	@echo "backend ready -> backend/.venv"

install-frontend: ## Install frontend dependencies
	$(NPM) install

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

test: test-backend ## Run all tests (frontend tests added in Phase 7)

test-backend: ## Run the backend pytest suite (unit only, no network)
	cd backend && .venv/bin/pytest -q -m "not integration"

test-integration: ## Run the opt-in live-API smoke tests
	cd backend && .venv/bin/pytest -q -m integration

lint: lint-backend lint-frontend ## Run every linter and type checker

lint-backend: ## ruff check + format check
	$(RUFF) check backend
	$(RUFF) format --check backend

lint-frontend: ## eslint + tsc typecheck
	$(NPM) run lint
	$(NPM) run typecheck

format: ## Auto-format everything
	$(RUFF) check --fix backend
	$(RUFF) format backend
	$(NPM) run format

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

security: ## Secret scan every tracked file
	@bash scripts/security-check.sh --full

security-selftest: ## Prove every secret-scan pattern still matches
	@bash scripts/security-check.sh --self-test

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

dev: ## Run backend (:8000) and frontend (:5173) together
	@echo "backend  -> http://localhost:8000"
	@echo "frontend -> http://localhost:5173"

dev-backend: ## Run the Django dev server
	cd backend && .venv/bin/python manage.py runserver 8000

dev-frontend: ## Run the Vite dev server
	$(NPM) run dev

up: ## docker compose up (full stack)
	docker compose up --build

down: ## docker compose down
	docker compose down

logs: ## Tail container logs
	docker compose logs -f

bench: ## Measure live API latencies (BE-60)
	cd backend && .venv/bin/python manage.py bench

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

clean: ## Remove caches and build artefacts
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/**/__pycache__ frontend/node_modules/.vite
	find backend -name '__pycache__' -type d -prune -exec rm -rf {} +
