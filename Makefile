.PHONY: help setup infra-up infra-down seed-data reseed-data append-data _truncate-raw dbt-run feast-apply materialize train train-only train-isolated train-only-isolated start-api start-api-dev stop-api stream-events score-test load-test load-test-ui clean export-to-duckdb offline-pipeline migrate-db mlflow-ui promote-model alias-model list-models docker-stats push-artifacts deploy-aws deploy-push deploy-init deploy-stop deploy-start deploy-terminate deploy-local deploy-local-down train-docker train-docker-watch stream-docker stream-docker-stop ssm-setup ssm-shell ssm-tunnel ssm-tunnel-mlflow ssm-tunnel-locust start-remote-locust cluster-up cluster-down cluster-status argocd-password argocd-ui

CONDA_ENV := fraud-realtime-ml
CONDA_PREFIX := $(shell conda info --base)/envs/$(CONDA_ENV)
PYTHON := $(CONDA_PREFIX)/bin/python
DBT := $(CONDA_PREFIX)/bin/dbt
FEAST := $(CONDA_PREFIX)/bin/feast
UVICORN := $(CONDA_PREFIX)/bin/uvicorn
GUNICORN := $(CONDA_PREFIX)/bin/gunicorn
MLFLOW := $(CONDA_PREFIX)/bin/mlflow
MLFLOW_PORT ?= 5000
MLFLOW_STORE ?= sqlite:///mlflow.db

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
	@echo "  make train SAMPLE=0.3       Train on a 30%% random sample (faster iteration)"
	@echo "  make train-only             Skip dataset build — reuse existing parquet, just train + evaluate"
	@echo "  make train-only CONFIG=training/experiments/lgbm_v1.yaml  Swap model without rebuilding dataset"
	@echo "  make mlflow-ui              Open MLflow experiment tracking UI (http://localhost:5000)"
	@echo "  make mlflow-ui MLFLOW_PORT=5001  Use a custom port"
	@echo "  make list-models            List recent MLflow runs with ROC-AUC / PR-AUC for comparison"
	@echo "  make promote-model RUN_ID=<id>         Promote a run to be the active /score model"
	@echo "  make promote-model RUN_ID=<id> ALIAS=production  Promote with a custom alias"
	@echo "  make promote-model MODEL_NAME=fraud_model VERSION=3  Promote a registry version"
	@echo "  make promote-model RUN_ID=<id> DRY_RUN=1  Preview without making changes"	@echo "  make alias-model MODEL=<name> VERSION=<n> ALIAS=<alias>  Set alias without re-promoting"
	@echo "    Common aliases: champion | challenger | archived"
	@echo "    e.g.  make alias-model MODEL=lgbm_fraud_model VERSION=7 ALIAS=challenger"
	@echo "    e.g.  make alias-model MODEL=rf_fraud_model VERSION=1 ALIAS=archived"	@echo "  make start-api      Start the FastAPI scoring service"
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

# Export raw Postgres tables → DuckDB offline store
export-to-duckdb:
	$(PYTHON) scripts/export_pg_to_duckdb.py $(if $(DB_PATH),--db-path $(DB_PATH),)

dbt-run:
	cd dbt_project && $(DBT) run --profiles-dir . --target duckdb
	cd dbt_project && $(DBT) test --profiles-dir . --target duckdb

# Export DuckDB feature tables to Parquet + materialize into Redis
materialize:
	$(PYTHON) scripts/materialize_features.py $(if $(DAYS),--days $(DAYS),)

dbt-docs:
	cd dbt_project && $(DBT) docs generate --profiles-dir . --target duckdb
	cd dbt_project && $(DBT) docs serve --profiles-dir .

dbt-show:
	cd dbt_project && $(DBT) show --select $(MODEL) --profiles-dir . --target duckdb --limit $(or $(LIMIT),10)

feast-apply:
	cd feast_repo/feature_repo && $(FEAST) apply

# Full offline pipeline: export → dbt → feast materialize
offline-pipeline: export-to-duckdb dbt-run materialize

# Apply SQL migrations to Postgres (run once after upgrading)
POSTGRES_CONTAINER ?= fraud_postgres

migrate-db:
	@set -a && . ./.env 2>/dev/null || true && set +a && \
	for f in sql/migrations/*.sql; do \
		echo "Applying $$f …"; \
		docker exec -i $(POSTGRES_CONTAINER) \
			env PGPASSWORD="$${POSTGRES_PASSWORD:-fraud_pass}" \
			psql \
				-U "$${POSTGRES_USER:-fraud_user}" \
				-d "$${POSTGRES_DB:-fraud_db}" \
			< "$$f" || exit 1; \
	done
	@echo "All migrations applied."

train:
	$(PYTHON) training/build_training_dataset.py $(if $(DB_PATH),--db-path $(DB_PATH),) $(if $(SAMPLE),--sample-frac $(SAMPLE),)
	$(PYTHON) training/train_model.py $(if $(CONFIG),--config $(CONFIG),)
	$(PYTHON) training/evaluate_model.py

train-only:
	@test -f training/datasets/training_dataset.parquet \
		|| (echo "ERROR: training/datasets/training_dataset.parquet not found. Run 'make train' first."; exit 1)
	$(PYTHON) training/train_model.py $(if $(CONFIG),--config $(CONFIG),)
	$(PYTHON) training/evaluate_model.py

# Demo: run full training pipeline capped at 4 cores (400%) via cpulimit.
# Simulates the "training node pool" CPU budget on a single machine.
# Install: brew install cpulimit
train-isolated:
	@command -v cpulimit >/dev/null 2>&1 || (echo "cpulimit not found — run: brew install cpulimit"; exit 1)
	@echo "[DEMO] Training node plane — capped at 400% CPU (4 cores)"
	cpulimit --limit 400 --include-children -- \
		sh -c '$(PYTHON) training/build_training_dataset.py $(if $(DB_PATH),--db-path $(DB_PATH),) $(if $(SAMPLE),--sample-frac $(SAMPLE),) && \
		       $(PYTHON) training/train_model.py $(if $(CONFIG),--config $(CONFIG),) && \
		       $(PYTHON) training/evaluate_model.py'

# Demo: train-only variant (reuses existing parquet) capped at 4 cores.
train-only-isolated:
	@command -v cpulimit >/dev/null 2>&1 || (echo "cpulimit not found — run: brew install cpulimit"; exit 1)
	@test -f training/datasets/training_dataset.parquet \
		|| (echo "ERROR: training/datasets/training_dataset.parquet not found. Run 'make train' first."; exit 1)
	@echo "[DEMO] Training node plane — capped at 400% CPU (4 cores)"
	cpulimit --limit 400 --include-children -- \
		sh -c '$(PYTHON) training/train_model.py $(if $(CONFIG),--config $(CONFIG),) && \
		       $(PYTHON) training/evaluate_model.py'

# Demo: live per-process CPU/memory usage (works with native Homebrew services + gunicorn).
# Open in a dedicated terminal during the demo. Updates every 2 seconds.
# Serving plane: gunicorn (API), postgres, redis-server
# Training plane: python training scripts
docker-stats:
	@echo "NOTE: Running without Docker — showing native process stats (serving vs training plane)"
	@echo "-------------------------------------------------------------------------------------"
	@while true; do \
		clear; \
		echo "=== SERVING PLANE ==="; \
		printf "%-40s %6s %6s\n" "PROCESS" "%CPU" "%MEM"; \
		ps aux | awk '/gunicorn|uvicorn/ && !/awk|grep/' | \
			awk '{cmd=$$0; sub(/.*gunicorn /,"gunicorn ",cmd); sub(/.*uvicorn /,"uvicorn ",cmd); printf "%-40s %6s %6s\n", substr(cmd,1,40), $$3, $$4}'; \
		ps aux | awk '/postgres/ && !/awk|grep/' | head -2 | \
			awk '{printf "%-40s %6s %6s\n", "postgres", $$3, $$4}'; \
		ps aux | awk '/redis-server/ && !/awk|grep/' | head -1 | \
			awk '{printf "%-40s %6s %6s\n", "redis-server", $$3, $$4}'; \
		echo ""; \
		echo "=== TRAINING PLANE ==="; \
		printf "%-40s %6s %6s\n" "PROCESS" "%CPU" "%MEM"; \
		FOUND=0; \
		ps aux | awk '/train_model|build_training|evaluate_model/ && !/awk|grep/' | while read line; do \
			FOUND=1; \
			echo "$$line" | awk '{for(i=11;i<=NF;i++) cmd=cmd" "$$i; printf "%-40s %6s %6s\n", substr(cmd,1,40), $$3, $$4}'; \
		done; \
		ps aux | awk '/cpulimit/ && !/awk|grep/' | head -1 | \
			awk '{printf "%-40s %6s %6s\n", "cpulimit [training guard]", $$3, $$4}'; \
		ps aux | grep -qE "train_model|build_training|evaluate_model" || echo "(no training job running)"; \
		echo ""; \
		echo "Updated: $$(date '+%H:%M:%S') — Ctrl+C to exit"; \
		sleep 2; \
	done

mlflow-ui:
	@echo "Opening MLflow UI → http://localhost:$(MLFLOW_PORT)"
	$(MLFLOW) ui --backend-store-uri $(MLFLOW_STORE) --host 0.0.0.0 --port $(MLFLOW_PORT)

list-models:
	$(PYTHON) scripts/promote_model.py --list $(if $(N),-n $(N),)

promote-model:
	$(PYTHON) scripts/promote_model.py \
		$(if $(RUN_ID),--run-id $(RUN_ID),) \
		$(if $(MODEL_NAME),--model-name $(MODEL_NAME),) \
		$(if $(VERSION),--version $(VERSION),) \
		$(if $(ALIAS),--alias $(ALIAS),) \
		$(if $(DRY_RUN),--dry-run,)

alias-model:
	@if [ -z "$(MODEL)" ] || [ -z "$(VERSION)" ] || [ -z "$(ALIAS)" ]; then \
		echo "Usage: make alias-model MODEL=<registry_name> VERSION=<n> ALIAS=<alias>"; \
		echo "  Common aliases: champion | challenger | archived"; \
		echo "  Example: make alias-model MODEL=lgbm_fraud_model VERSION=7 ALIAS=challenger"; \
		echo "  Example: make alias-model MODEL=rf_fraud_model VERSION=1 ALIAS=archived"; \
		exit 1; \
	fi
	$(PYTHON) scripts/promote_model.py \
		--set-alias \
		--model-name $(MODEL) \
		--version $(VERSION) \
		--alias $(ALIAS) \
		$(if $(DRY_RUN),--dry-run,)

# Async uvicorn workers handle concurrency via the event loop — 4 workers
# saturate most machines.  Override: make start-api API_WORKERS=6
API_WORKERS ?= 4

start-api:
	OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
	$(GUNICORN) app.main:app \
		-w $(API_WORKERS) \
		-k uvicorn.workers.UvicornWorker \
		--bind 0.0.0.0:8000 \
		--worker-connections 1000 \
		--backlog 2048 \
		--timeout 30 \
		--access-logfile -

start-api-dev:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

stop-api:
	@lsof -ti :8000 | xargs kill 2>/dev/null && echo "API stopped" || echo "Nothing running on port 8000"

# Load testing — targets ~300 TPS by default
# Override: make load-test USERS=500 RATE=50 DURATION=120s API_HOST=http://localhost:8000
LOCUST   := $(CONDA_PREFIX)/bin/locust
USERS    ?= 500
RATE     ?= 50
DURATION ?= 60s
API_HOST ?= http://localhost:8000

load-test:
	$(LOCUST) -f locustfile.py --headless \
		-u $(USERS) -r $(RATE) \
		--run-time $(DURATION) \
		--host $(API_HOST) \
		--only-summary

load-test-ui:
	@echo "Open http://localhost:8089 in your browser, then configure users + host"
	$(LOCUST) -f locustfile.py --host $(API_HOST)

stream-events:
	$(PYTHON) simulator/stream_transactions.py

score-test:
	curl -s -X POST http://localhost:8000/score \
		-H "Content-Type: application/json" \
		-d '{"transaction_id":"test-001","user_id":"u_000001","device_id":"d_0000001","merchant_id":"m_00001","amount":250.00,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
		| python3 -m json.tool

lint:
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf models/*.pkl models/*.json
	rm -rf dbt_project/target dbt_project/logs dbt_project/.user.yml
	rm -rf training/datasets/
	rm -rf data/duckdb/*.duckdb data/duckdb/*.wal data/duckdb/parquet/*.parquet
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ==============================================================================
# Cloud Deployment (AWS)
# ==============================================================================

## Push ML artifacts (parquet, model, registry) to S3
push-artifacts:
	@echo "Pushing ML artifacts to S3..."
	chmod +x deploy/push-artifacts.sh
	./deploy/push-artifacts.sh

## Deploy code + pull artifacts from S3 + restart services (lean — no data rebuild)
deploy-push:
	@echo "Deploying project via SSM (no SSH required)..."
	./deploy/push-to-server-ssm.sh $(EC2_HOST)

## First-time: seed EC2 Postgres with reference + historical data
deploy-init:
	@echo "Seeding remote Postgres (one-time)..."
	chmod +x deploy/init-remote-db.sh
	./deploy/init-remote-db.sh

deploy-aws:
	@echo "Deploying Fraud ML Demo to AWS..."
	./deploy/deploy-aws.sh $(REGION) $(INSTANCE_TYPE)

## One-time: attach SSM IAM role to running instance
ssm-setup:
	@echo "Setting up SSM access..."
	chmod +x deploy/setup-ssm.sh
	./deploy/setup-ssm.sh $(EC2_HOST)

## Interactive shell on EC2 over HTTPS (no SSH)
ssm-shell:
	aws ssm start-session --region $$(grep REGION deploy/.instance-info | cut -d= -f2) \
	    --target $$(grep INSTANCE_ID deploy/.instance-info | cut -d= -f2)

## Port-forward EC2 API to localhost:8000 (then run: make load-test-ui API_HOST=http://localhost:8000)
ssm-tunnel:
	chmod +x deploy/ssm-tunnel.sh
	./deploy/ssm-tunnel.sh "" 8000 8000

## Port-forward MLflow to localhost:5000
ssm-tunnel-mlflow:
	chmod +x deploy/ssm-tunnel.sh
	./deploy/ssm-tunnel.sh "" 5000 5000

## Port-forward Locust UI to localhost:8089 (load test runs ON EC2 — true latency)
ssm-tunnel-locust:
	chmod +x deploy/ssm-tunnel.sh
	./deploy/ssm-tunnel.sh "" 8089 8089

## Start Locust on EC2 (run before ssm-tunnel-locust)
start-remote-locust:
	@echo "Starting Locust load tester on EC2..."
	aws ssm send-command \
		--region $$(grep REGION deploy/.instance-info | cut -d= -f2) \
		--instance-id $$(grep INSTANCE_ID deploy/.instance-info | cut -d= -f2) \
		--document-name AWS-RunShellScript \
		--parameters 'commands=["cd /home/ubuntu/fraud-realtime-ml-prototype && docker compose -f deploy/docker-compose.prod.yml --profile loadtest up -d --force-recreate locust"]' \
		--output text --query Command.CommandId
	@echo "Locust UI available at EC2:8089 — run: make ssm-tunnel-locust"

deploy-stop:
	./deploy/stop-server.sh --stop

deploy-start:
	./deploy/stop-server.sh --start

deploy-terminate:
	./deploy/stop-server.sh --terminate

# Build and run production stack locally (for testing before cloud deploy)
deploy-local:
	docker compose -f deploy/docker-compose.prod.yml up -d --build
	@echo ""
	@echo "API running at http://localhost:8000"
	@echo "Run: make load-test"

deploy-local-down:
	docker compose -f deploy/docker-compose.prod.yml down

# Run training pipeline inside Docker (isolated from API, capped at 4 CPU / 6GB RAM)
# Usage: make train-docker
#        make train-docker CONFIG=training/experiments/lgbm_optimized_hyperparams.yaml
#        make train-docker SAMPLE=0.3
train-docker:
	@echo "[DOCKER] Starting isolated training container (4 CPU / 6GB RAM limit)..."
	CONFIG=$(CONFIG) SAMPLE=$(SAMPLE) \
	docker compose -f deploy/docker-compose.prod.yml \
		--profile training run --rm training
	@echo "[DOCKER] Training complete. Model artifacts saved to models/"

# Run training + show live resource usage side-by-side
train-docker-watch:
	@echo "Open another terminal and run: make docker-stats"
	$(MAKE) train-docker CONFIG=$(CONFIG) SAMPLE=$(SAMPLE)

# Start transaction stream simulator inside Docker (writes to Redis sorted sets)
# Usage: make stream-docker
#        make stream-docker EPS=20
stream-docker:
	@echo "[DOCKER] Starting transaction stream simulator ($(or $(EPS),10) events/sec)..."
	SIM_EVENTS_PER_SECOND=$(or $(EPS),10) \
	docker compose -f deploy/docker-compose.prod.yml \
		--profile simulator up -d simulator
	@echo "[DOCKER] Simulator running. View logs: docker logs -f fraud_simulator"

stream-docker-stop:
	docker compose -f deploy/docker-compose.prod.yml --profile simulator stop simulator
	@echo "[DOCKER] Simulator stopped."

# ============================================================================
# Slice A1 — k8s substrate + GitOps (k3d + ArgoCD)
# ============================================================================
K3D_CLUSTER_NAME := fraud-platform
K3D_CONFIG := infra/k8s/bootstrap/k3d-cluster.yaml
ARGOCD_VERSION := v2.13.0
ARGOCD_MANIFEST := infra/k8s/bootstrap/argocd/install.yaml
ROOT_APP := infra/k8s/apps/dev/root-app.yaml

cluster-up:
	@command -v k3d >/dev/null || { echo "k3d not installed"; exit 1; }
	@command -v kubectl >/dev/null || { echo "kubectl not installed"; exit 1; }
	@if k3d cluster list -o json 2>/dev/null | grep -q '"name": *"$(K3D_CLUSTER_NAME)"'; then \
		echo "[k3d] Cluster $(K3D_CLUSTER_NAME) exists — skipping create"; \
	else \
		echo "[k3d] Creating cluster from $(K3D_CONFIG)..."; \
		k3d cluster create --config $(K3D_CONFIG); \
	fi
	@echo "[k8s] Waiting for nodes Ready..."
	@kubectl wait --for=condition=Ready nodes --all --timeout=120s
	@echo "[argocd] Installing $(ARGOCD_VERSION) from vendored manifest..."
	@kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -n argocd -f $(ARGOCD_MANIFEST) >/dev/null
	@echo "[argocd] Waiting for deployments Available..."
	@kubectl -n argocd wait --for=condition=Available deployment --all --timeout=300s
	@echo "[gitops] Applying root Application..."
	@kubectl apply -f $(ROOT_APP)
	@echo ""
	@echo "==== cluster-up complete ===="
	@$(MAKE) --no-print-directory cluster-status
	@echo ""
	@echo "Next: 'make argocd-password' to get admin password, 'make argocd-ui' to open UI"

cluster-down:
	@k3d cluster delete $(K3D_CLUSTER_NAME) 2>/dev/null || echo "[k3d] Cluster $(K3D_CLUSTER_NAME) not present"

cluster-status:
	@echo "-- Nodes --"
	@kubectl get nodes 2>/dev/null || echo "(no cluster)"
	@echo "-- ArgoCD pods --"
	@kubectl -n argocd get pods 2>/dev/null || echo "(argocd not installed)"
	@echo "-- ArgoCD Applications --"
	@kubectl -n argocd get applications 2>/dev/null || true
	@echo "-- Deployments (default ns) --"
	@kubectl get deployments 2>/dev/null || true

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d && echo

argocd-ui:
	@echo "ArgoCD UI: https://localhost:8080  (accept self-signed cert)"
	@echo "  user: admin"
	@echo "  pass: run 'make argocd-password' in another terminal"
	@kubectl -n argocd port-forward svc/argocd-server 8080:443
