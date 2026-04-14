.PHONY: help setup infra-up infra-down seed-data reseed-data append-data _truncate-raw dbt-run feast-apply materialize train start-api stream-events score-test clean export-to-duckdb offline-pipeline migrate-db

CONDA_ENV := fraud-realtime-ml
CONDA_PREFIX := $(shell conda info --base)/envs/$(CONDA_ENV)
PYTHON := $(CONDA_PREFIX)/bin/python
DBT := $(CONDA_PREFIX)/bin/dbt
FEAST := $(CONDA_PREFIX)/bin/feast
UVICORN := $(CONDA_PREFIX)/bin/uvicorn

help:
	@echo "Fraud Realtime ML MVP — available commands:"
	@echo ""
	@echo "  make setup          Install Python dependencies"
	@echo "  make infra-up       Start Postgres and Redis via Docker Compose"
	@echo "  make infra-down     Stop and remove containers"
	@echo "  make seed-data               Generate synthetic raw data into Postgres (first-time setup)"
	@echo "  make seed-data START_DATE=2025-01-01 END_DATE=2026-03-31   Custom timeframe"
	@echo "  make seed-data FRAUD_RATE_MIN=0.008 FRAUD_RATE_MAX=0.02   Custom fraud rate range"
	@echo "  make reseed-data             TRUNCATE all raw tables then re-seed from scratch"
	@echo "  make reseed-data START_DATE=2025-01-01 END_DATE=2026-03-31 FRAUD_RATE_MIN=0.008 FRAUD_RATE_MAX=0.02"
	@echo "  make append-data             Append new generated data on top of existing rows"
	@echo "  make append-data START_DATE=2026-04-01 END_DATE=2026-06-30  Extend existing dataset"
	@echo ""
	@echo "  ── Offline pipeline (DuckDB) ─────────────────────────────────────"
	@echo "  make export-to-duckdb        Export Postgres raw tables → DuckDB offline store"
	@echo "  make dbt-run                 Run dbt models against DuckDB (staging → features)"
	@echo "  make feast-apply             Register versioned Feast feature views"
	@echo "  make materialize             Export Parquet + push offline features → Redis (auto-detects data range)"
	@echo "  make offline-pipeline        Run full offline pipeline end-to-end (export → dbt → materialize)"
	@echo "  make migrate-db              Apply SQL migrations to Postgres (adds new columns etc.)"
	@echo ""
	@echo "  ── Training & serving ────────────────────────────────────────────"
	@echo "  make train          Build training dataset (Feast PIT join + online_feature_log) + train model"
	@echo "  make train CONFIG=training/my_config.yaml  Train with a custom config"
	@echo "  make start-api      Start the FastAPI scoring service"
	@echo "  make stream-events  Start the transaction stream simulator"
	@echo "  make score-test     Send a test scoring request"
	@echo "  make clean          Remove generated artifacts"
	@echo ""

setup:
	$(CONDA_PREFIX)/bin/pip install -r requirements.txt
	cp -n .env.example .env || true

infra-up:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose up -d postgres redis && sleep 3 && docker compose ps; \
	else \
		$(MAKE) infra-up-native; \
	fi

infra-up-native:
	@echo "Starting Postgres and Redis via Homebrew services..."
	@brew services start postgresql@15 2>/dev/null || true
	@brew services start redis 2>/dev/null || true
	@sleep 3
	@echo "--- Services status ---"
	@pg_isready -h localhost -p 5432 && echo "Postgres: OK" || echo "Postgres: FAILED"
	@redis-cli ping && echo "Redis: OK" || echo "Redis: FAILED"

infra-down:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose down; \
	else \
		brew services stop postgresql@15 || true; \
		brew services stop redis || true; \
	fi

infra-reset:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose down -v; \
	else \
		brew services stop postgresql@15 || true; \
		rm -rf /tmp/fraud_pgdata; \
		brew services stop redis || true; \
	fi

seed-data:
	$(PYTHON) simulator/generate_reference_data.py
	$(PYTHON) simulator/generate_historical_transactions.py \
		$(if $(START_DATE),--start-date $(START_DATE),) \
		$(if $(END_DATE),--end-date $(END_DATE),) \
		$(if $(FRAUD_RATE_MIN),--fraud-rate-min $(FRAUD_RATE_MIN),) \
		$(if $(FRAUD_RATE_MAX),--fraud-rate-max $(FRAUD_RATE_MAX),) \
		$(if $(SEED),--seed $(SEED),)

# Truncate all raw + label tables (schema is preserved)
_truncate-raw:
	@echo "Truncating all raw and label tables..."
	@set -a && . ./.env 2>/dev/null || true && set +a && \
	PGPASSWORD="$${POSTGRES_PASSWORD:-fraud_pass}" psql \
		-h "$${POSTGRES_HOST:-localhost}" \
		-p "$${POSTGRES_PORT:-5432}" \
		-U "$${POSTGRES_USER:-fraud_user}" \
		-d "$${POSTGRES_DB:-fraud_db}" \
		-c "TRUNCATE TABLE fraud_labels, raw_transactions, raw_login_events, raw_devices, raw_users, raw_merchants, model_score_log RESTART IDENTITY CASCADE;"
	@echo "All raw tables cleared."

# Wipe all raw tables and regenerate from scratch
reseed-data: _truncate-raw seed-data

# Append new generated data on top of existing rows (reference data deduped via ON CONFLICT DO NOTHING)
append-data:
	$(PYTHON) simulator/generate_reference_data.py
	$(PYTHON) simulator/generate_historical_transactions.py \
		$(if $(START_DATE),--start-date $(START_DATE),) \
		$(if $(END_DATE),--end-date $(END_DATE),) \
		$(if $(FRAUD_RATE_MIN),--fraud-rate-min $(FRAUD_RATE_MIN),) \
		$(if $(FRAUD_RATE_MAX),--fraud-rate-max $(FRAUD_RATE_MAX),) \
		$(if $(SEED),--seed $(SEED),)

dbt-run:
	cd dbt_project && $(DBT) run --profiles-dir . --target duckdb
	cd dbt_project && $(DBT) test --profiles-dir . --target duckdb

dbt-docs:
	cd dbt_project && $(DBT) docs generate --profiles-dir . --target duckdb
	cd dbt_project && $(DBT) docs serve --profiles-dir .

dbt-show:
	cd dbt_project && $(DBT) show --select $(MODEL) --profiles-dir . --target duckdb --limit $(or $(LIMIT),10)

feast-apply:
	cd feast_repo/feature_repo && $(FEAST) apply

# Export DuckDB feature tables to Parquet + materialize into Redis
materialize:
	$(PYTHON) scripts/materialize_features.py $(if $(DAYS),--days $(DAYS),)

# Export raw Postgres tables → DuckDB offline store
export-to-duckdb:
	$(PYTHON) scripts/export_pg_to_duckdb.py $(if $(DB_PATH),--db-path $(DB_PATH),)

# Full offline pipeline: export → dbt → feast materialize
offline-pipeline: export-to-duckdb dbt-run materialize

# Apply SQL migrations to Postgres (run once after upgrading)
migrate-db:
	@set -a && . ./.env 2>/dev/null || true && set +a && \
	PGPASSWORD="$${POSTGRES_PASSWORD:-fraud_pass}" psql \
		-h "$${POSTGRES_HOST:-localhost}" \
		-p "$${POSTGRES_PORT:-5432}" \
		-U "$${POSTGRES_USER:-fraud_user}" \
		-d "$${POSTGRES_DB:-fraud_db}" \
		-f sql/migrations/02_add_feature_service_version.sql
	@echo "Migration applied."

train:
	$(PYTHON) training/build_training_dataset.py $(if $(DB_PATH),--db-path $(DB_PATH),)
	$(PYTHON) training/train_model.py $(if $(CONFIG),--config $(CONFIG),)
	$(PYTHON) training/evaluate_model.py

start-api:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

stream-events:
	$(PYTHON) simulator/stream_transactions.py

score-test:
	curl -s -X POST http://localhost:8000/score \
		-H "Content-Type: application/json" \
		-d '{"transaction_id":"test-001","user_id":"u_001","device_id":"d_001","merchant_id":"m_001","amount":250.00,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
		| python3 -m json.tool

lint:
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf models/*.pkl models/*.json
	rm -rf dbt_project/target dbt_project/logs dbt_project/.user.yml
	rm -rf training/datasets/
	rm -rf data/duckdb/*.duckdb data/duckdb/*.wal data/duckdb/parquet/*.parquet
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
