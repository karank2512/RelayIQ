SHELL := /bin/bash
PY := apps/api/.venv/bin/python
PIP := apps/api/.venv/bin/pip
COMPOSE := docker compose

.PHONY: setup dev seed test test-unit test-integration test-e2e lint typecheck migrate \
        benchmark load-test down api worker dashboard fmt docs openapi calibration

## setup: create venv, install backend + frontend deps
setup:
	python3 -m venv apps/api/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "apps/api[dev]"
	cd apps/dashboard && npm install

## dev: full local stack (postgres, redis, api, worker, dashboard, prometheus, grafana)
dev:
	$(COMPOSE) up --build

## dev-deps: just postgres + redis for host-run api/worker
dev-deps:
	$(COMPOSE) up -d postgres redis

api:
	cd apps/api && .venv/bin/uvicorn relayiq.main:app --reload --port 8000

worker:
	cd apps/api && .venv/bin/celery -A relayiq.workers.celery_app worker -l info -Q enrichment,sync

dashboard:
	cd apps/dashboard && npm run dev

## migrate: apply alembic migrations
migrate:
	cd apps/api && .venv/bin/alembic upgrade head

## seed: generate synthetic world + seed database (idempotent)
seed:
	cd apps/api && .venv/bin/python -m relayiq.seed.cli --reset

test: test-unit

test-unit:
	cd apps/api && .venv/bin/pytest tests/unit -q

test-integration:
	cd apps/api && .venv/bin/pytest tests/integration -q

test-e2e:
	cd apps/api && .venv/bin/pytest tests/e2e -q

test-all:
	cd apps/api && .venv/bin/pytest tests -q

lint:
	cd apps/api && .venv/bin/ruff check relayiq tests
	cd apps/dashboard && npm run lint

fmt:
	cd apps/api && .venv/bin/ruff format relayiq tests && .venv/bin/ruff check --fix relayiq tests

typecheck:
	cd apps/api && .venv/bin/mypy relayiq
	cd apps/dashboard && npm run typecheck

## benchmark: run the seeded strategy comparison (naive vs RelayIQ etc.)
benchmark:
	cd apps/api && .venv/bin/python -m relayiq.benchmark.cli --out ../../docs/benchmarks/results.json

calibration:
	cd apps/api && .venv/bin/python -m relayiq.benchmark.calibration --out ../../docs/benchmarks/calibration.json

## load-test: locust headless against local API
load-test:
	cd apps/api && .venv/bin/locust -f ../../tests/load/locustfile.py --headless -u 25 -r 5 -t 60s \
		--host http://localhost:8000 --csv ../../docs/benchmarks/load

openapi:
	cd apps/api && .venv/bin/python -m relayiq.scripts.export_openapi ../../docs/api/openapi.json

down:
	$(COMPOSE) down -v
