.PHONY: help setup infra-up infra-down seed-data dbt-run feast-apply materialize train start-api stream-events score-test clean

CONDA_ENV := fraud-realtime-ml
CONDA_PREFIX := /opt/anaconda3/envs/$(CONDA_ENV)
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
	@echo "  make seed-data      Generate synthetic raw data into Postgres"
	@echo "  make dbt-run        Run all dbt models"
	@echo "  make feast-apply    Apply Feast feature definitions"
	@echo "  make materialize    Materialize offline features to Redis via Feast"
	@echo "  make train          Train the baseline fraud model"
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
	$(PYTHON) simulator/generate_historical_transactions.py

dbt-run:
	cd dbt_project && $(DBT) run --profiles-dir .
	cd dbt_project && $(DBT) test --profiles-dir .

dbt-docs:
	cd dbt_project && $(DBT) docs generate --profiles-dir .
	cd dbt_project && $(DBT) docs serve --profiles-dir .

dbt-show:
	cd dbt_project && $(DBT) show --select $(MODEL) --profiles-dir . --limit $(or $(LIMIT),10)

feast-apply:
	cd feast_repo/feature_repo && $(FEAST) apply

materialize:
	$(PYTHON) feast_repo/materialize.py

train:
	$(PYTHON) training/build_training_dataset.py
	$(PYTHON) training/train_model.py
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
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
