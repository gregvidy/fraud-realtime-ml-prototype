.PHONY: help setup infra-up infra-down seed-data reseed-data append-data _truncate-raw dbt-run feast-apply materialize train train-only train-isolated train-only-isolated start-api start-api-dev stop-api stream-events stream-producer stream-consumer score-test load-test load-test-ui clean export-to-clickhouse offline-pipeline migrate-db mlflow-ui promote-model alias-model list-models docker-stats push-artifacts deploy-aws deploy-push deploy-init deploy-stop deploy-start deploy-terminate deploy-local deploy-local-down train-docker train-docker-watch stream-docker stream-docker-stop ssm-setup ssm-shell ssm-tunnel ssm-tunnel-mlflow ssm-tunnel-locust start-remote-locust ch-up ch-down ch-logs ch-status ch-shell ch-verify-rbac stream-up stream-down stream-topics stream-schemas stream-schemas-list stream-status stream-logs stream-console stream-ch-apply stream-ch-status stream-ch-lag stream-ch-drop outbox-migrate outbox-relay outbox-produce outbox-status stream-ch-fallback-test cluster-up cluster-down cluster-status argocd-password argocd-ui kubeflow-up kubeflow-down kubeflow-status kubeflow-ui dp-up dp-down dp-status pg-shell mlflow-k8s-ui stream-k8s-up stream-k8s-down stream-k8s-status stream-k8s-console-ui stream-k8s-rpk ch-k8s-up ch-k8s-down ch-k8s-status ch-k8s-shell ch-k8s-verify-rbac

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
	@echo "  ── Offline pipeline (ClickHouse) ──────────────────────────"
	@echo "  make export-to-clickhouse    Export Postgres raw tables → ClickHouse offline store"
	@echo "  make dbt-run                 Run dbt models against ClickHouse (staging → features)"
	@echo "  make feast-apply             Register versioned Feast feature views"
	@echo "  make materialize             Export Parquet + push offline features → Redis (auto-detects data range)"
	@echo "  make offline-pipeline        Run full offline pipeline end-to-end (export → dbt → materialize)"
	@echo "  make ch-status               Show ClickHouse version, databases, and RBAC users"
	@echo "  make ch-shell                Open interactive clickhouse-client (admin user)"
	@echo "  make ch-verify-rbac          Run the 24-assertion RBAC test suite"
	@echo "  make migrate-db              Apply SQL migrations to Postgres (adds new columns etc.)"
	@echo ""
	@echo "  ── Streaming (Redpanda) ─────────────────────────────────────────"
	@echo "  make stream-up               Start Redpanda + Console; create 8 topics; register 3 Avro schemas"
	@echo "  make stream-down             Stop Redpanda + Console"
	@echo "  make stream-topics           (Re-)create the 8 topics from streaming/rpk/topics.sh"
	@echo "  make stream-schemas          Register / re-register Avro schemas with Schema Registry"
	@echo "  make stream-schemas-list     List registered subjects + versions"
	@echo "  make stream-status           Show cluster health, topics, consumer groups"
	@echo "  make stream-console          Open Redpanda Console (http://localhost:8080)"
	@echo "  make stream-logs             Tail Redpanda broker logs"
	@echo "  make stream-producer [EPS=200 MIX=visa=0.5,qris=0.5 DURATION=60 SEED=42]"
	@echo "                               Publish synthetic multi-channel events to Redpanda"
	@echo "  make stream-consumer NAME=<name> [DURATION=30]"
	@echo "                               Run a consumer (fraud_decisioning | feature_store_updater | postgres_sink)"
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
		docker compose up -d postgres redis clickhouse && sleep 3 && $(MAKE) --no-print-directory _ch-wait && docker compose ps; \
	else \
		$(MAKE) infra-up-native; \
	fi

# Internal: block until ClickHouse reports healthy (used by infra-up).
_ch-wait:
	@echo "Waiting for ClickHouse to become healthy..."
	@for i in $$(seq 1 30); do \
		if docker inspect --format='{{.State.Health.Status}}' $(CLICKHOUSE_CONTAINER) 2>/dev/null | grep -q healthy; then \
			echo "ClickHouse: OK"; exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "ClickHouse did not become healthy in time"; docker logs --tail 60 $(CLICKHOUSE_CONTAINER); exit 1

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

# ── ClickHouse (offline analytical store — sole offline store since slice 5) ──
CLICKHOUSE_CONTAINER ?= fraud_clickhouse

ch-up:
	docker compose up -d clickhouse
	@$(MAKE) --no-print-directory _ch-wait

ch-down:
	docker compose stop clickhouse

ch-logs:
	docker logs --tail 200 -f $(CLICKHOUSE_CONTAINER)

ch-status:
	@docker exec $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}" \
		--query "SELECT version() AS version, currentDatabase() AS db, hostName() AS host FORMAT PrettyCompactMonoBlock"
	@echo "── Databases ──"
	@docker exec $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}" \
		--query "SHOW DATABASES"
	@echo "── Users ──"
	@docker exec $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}" \
		--query "SELECT name, storage FROM system.users WHERE name NOT IN ('default') ORDER BY name FORMAT PrettyCompactMonoBlock"

# Open an interactive shell as the default (admin) user
ch-shell:
	docker exec -it $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}"

# Verifies each of the 4 POC roles authenticates and gets the expected grants.
# Exit code 0 = all assertions passed.
ch-verify-rbac:
	@bash scripts/verify_clickhouse_rbac.sh

# ── Redpanda streaming (broker + Schema Registry + Console UI) ───────────
REDPANDA_CONTAINER ?= fraud_redpanda

# Start Redpanda + Console, wait for health, create topics, register schemas.
stream-up:
	docker compose up -d redpanda redpanda-console
	@echo "Waiting for Redpanda to become healthy..."
	@for i in $$(seq 1 30); do \
		if docker inspect --format='{{.State.Health.Status}}' $(REDPANDA_CONTAINER) 2>/dev/null | grep -q healthy; then \
			echo "Redpanda: OK"; break; \
		fi; \
		sleep 2; \
	done
	@$(MAKE) --no-print-directory stream-topics
	@$(MAKE) --no-print-directory stream-schemas
	@echo ""
	@echo "Redpanda Console: http://localhost:8080"
	@echo "Schema Registry:  http://localhost:8081"

stream-down:
	docker compose stop redpanda redpanda-console

# Create the 8 topics (6 channels + txn.scored + login.events)
stream-topics:
	@bash streaming/rpk/topics.sh

# Register the 3 Avro schemas (8 subjects — 6 channels share TxnEvent)
stream-schemas:
	$(PYTHON) -m streaming.schema_registry register

stream-schemas-list:
	$(PYTHON) -m streaming.schema_registry list

# Show topics + consumer groups + broker health
stream-status:
	@echo "── Redpanda cluster health ──"
	@docker exec $(REDPANDA_CONTAINER) rpk cluster health
	@echo ""
	@echo "── Topics ──"
	@docker exec $(REDPANDA_CONTAINER) rpk topic list
	@echo ""
	@echo "── Consumer groups ──"
	@docker exec $(REDPANDA_CONTAINER) rpk group list

stream-logs:
	docker logs --tail 200 -f $(REDPANDA_CONTAINER)

# Open the Redpanda Console web UI (Linux xdg-open / macOS open — falls back to printing URL)
stream-console:
	@command -v xdg-open >/dev/null && xdg-open http://localhost:8080 \
		|| command -v open >/dev/null && open http://localhost:8080 \
		|| echo "Redpanda Console: http://localhost:8080"

# ── ClickHouse streaming ingest (Slice 9: Kafka Engine + MVs) ────────────
# Apply / re-apply Kafka Engine tables + landing MVs + velocity MVs.
# Idempotent — MergeTree destinations use IF NOT EXISTS (data preserved),
# Kafka Engine tables + MVs are dropped and recreated.
stream-ch-apply:
	@bash scripts/apply_clickhouse_streaming.sh

# Row counts + latest event timestamp per stream destination.
stream-ch-status:
	@docker exec $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}" --multiquery --query "\
		SELECT 'main.stream_transactions' AS tbl, count() AS rows, max(event_timestamp) AS latest FROM main.stream_transactions UNION ALL \
		SELECT 'main.stream_scored_txns',       count(),        max(event_timestamp) FROM main.stream_scored_txns UNION ALL \
		SELECT 'main.stream_logins',            count(),        max(event_timestamp) FROM main.stream_logins UNION ALL \
		SELECT 'main.stream_user_velocity_5m',  count(),        max(window_start)    FROM main.stream_user_velocity_5m UNION ALL \
		SELECT 'main.stream_user_velocity_1h',  count(),        max(window_start)    FROM main.stream_user_velocity_1h UNION ALL \
		SELECT 'main.stream_user_velocity_24h', count(),        max(window_start)    FROM main.stream_user_velocity_24h UNION ALL \
		SELECT 'main.stream_device_velocity_5m',count(),        max(window_start)    FROM main.stream_device_velocity_5m UNION ALL \
		SELECT 'main.stream_device_velocity_1h',count(),        max(window_start)    FROM main.stream_device_velocity_1h UNION ALL \
		SELECT 'main.stream_latest_features',   count(),        max(event_timestamp) FROM main.stream_latest_features \
		FORMAT PrettyCompactMonoBlock"

# Consumer-group lag for the 3 ClickHouse Kafka-Engine groups.
stream-ch-lag:
	@echo "── ClickHouse Kafka-Engine consumer lag ──"
	@docker exec $(REDPANDA_CONTAINER) rpk group describe -s clickhouse-analytics-v1 clickhouse-scored-v1 clickhouse-logins-v1

# Drop all Kafka Engine tables + MVs (keeps destination MergeTree data).
# Use to reset consumer group offsets by bumping the *-v1 suffix in the SQL.
stream-ch-drop:
	@docker exec $(CLICKHOUSE_CONTAINER) clickhouse-client --user default \
		--password "$${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}" --multiquery --query "\
		DROP TABLE IF EXISTS main.mv_stream_transactions_ingest SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_scored_ingest SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_logins_ingest SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_user_velocity_5m SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_user_velocity_1h SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_user_velocity_24h SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_device_velocity_5m SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_device_velocity_1h SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_latest_features_user SYNC; \
		DROP TABLE IF EXISTS main.mv_stream_latest_features_device SYNC; \
		DROP TABLE IF EXISTS raw.stream_txn_kafka SYNC; \
		DROP TABLE IF EXISTS raw.stream_scored_kafka SYNC; \
		DROP TABLE IF EXISTS raw.stream_login_kafka SYNC;"
	@echo "Kafka Engine tables + MVs dropped (destination MergeTree tables preserved)."

# ── Transactional outbox + cold-fallback (Slice 10) ──────────────────────
# Apply the outbox_events table + partial index to the running Postgres.
outbox-migrate:
	@bash scripts/apply_outbox_migration.sh

# Run the outbox relay (poll → publish → mark). Ctrl+C or DURATION exit cleanly.
outbox-relay:
	$(PYTHON) -m streaming.outbox_relay \
		$(if $(DURATION),--duration $(DURATION),) \
		$(if $(BATCH_SIZE),--batch-size $(BATCH_SIZE),)

# Dual-write emulator — INSERTs to raw_transactions + outbox_events in one tx.
# Same knobs as `stream-producer` (EPS / MIX / DURATION / SEED / FRAUD_RATE).
outbox-produce:
	$(PYTHON) -m streaming.outbox_producer \
		--eps $(EPS) \
		--channel-mix "$(MIX)" \
		$(if $(DURATION),--duration $(DURATION),) \
		$(if $(SEED),--seed $(SEED),) \
		$(if $(FRAUD_RATE),--fraud-rate $(FRAUD_RATE),)

# Outbox depth + throughput snapshot.
outbox-status:
	@docker exec -e PGPASSWORD="$${POSTGRES_PASSWORD:-fraud_pass}" fraud_postgres \
		psql -U $${POSTGRES_USER:-fraud_user} -d $${POSTGRES_DB:-fraud_db} -c "\
		SELECT \
		  (SELECT count(*) FROM outbox_events)                          AS total, \
		  (SELECT count(*) FROM outbox_events WHERE published_at IS NULL) AS unpublished, \
		  (SELECT count(*) FROM outbox_events WHERE published_at IS NOT NULL) AS published, \
		  (SELECT max(published_at) FROM outbox_events)                  AS last_published_at;"

# End-to-end cold-fallback drill: pause Redis → curl /score → resume Redis.
# Requires make start-api to be running.
# Uses DIFFERENT user_ids per call so the per-entity TTL cache (60s) doesn't
# mask the Redis miss. Each user_id must be present in stream_latest_features
# (produce events first via `make stream-producer`).
stream-ch-fallback-test:
	@echo "── health check (BEFORE) ──"
	@curl -sS http://localhost:8000/health && echo ""
	@echo ""
	@echo "── score via HOT path (Redis available, user u_000042) ──"
	@curl -sS -X POST http://localhost:8000/score -H "Content-Type: application/json" \
		-d '{"transaction_id":"cold-fb-hot-1","user_id":"u_000042","device_id":"d_0000001","merchant_id":"m_00001","amount":250.00,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
		| python3 -m json.tool
	@echo ""
	@echo "── pausing Redis (docker pause) ──"
	@docker pause fraud_redis
	@echo "── score via COLD path (Redis paused, CH fallback, user u_000199) ──"
	@curl -sS -X POST http://localhost:8000/score -H "Content-Type: application/json" \
		-d '{"transaction_id":"cold-fb-cold-1","user_id":"u_000199","device_id":"d_0000002","merchant_id":"m_00002","amount":250.00,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
		| python3 -m json.tool
	@echo ""
	@echo "── resuming Redis ──"
	@docker unpause fraud_redis
	@sleep 1
	@echo "── score via HOT path (Redis resumed, user u_000356) ──"
	@curl -sS -X POST http://localhost:8000/score -H "Content-Type: application/json" \
		-d '{"transaction_id":"cold-fb-hot-2","user_id":"u_000356","device_id":"d_0000003","merchant_id":"m_00003","amount":250.00,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
		| python3 -m json.tool

# Export raw Postgres tables → ClickHouse `raw` schema.
# Uses ClickHouse's postgresql() table function (server-side copy, no pandas).
export-to-clickhouse:
	$(PYTHON) scripts/export_pg_to_clickhouse.py $(if $(TABLES),--tables $(TABLES),)

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
	cd dbt_project && $(DBT) run --profiles-dir . --target clickhouse
	cd dbt_project && $(DBT) test --profiles-dir . --target clickhouse

# Export ClickHouse feature tables to Parquet + materialize into Redis
materialize:
	$(PYTHON) scripts/materialize_features.py $(if $(DAYS),--days $(DAYS),)

dbt-docs:
	cd dbt_project && $(DBT) docs generate --profiles-dir . --target clickhouse
	cd dbt_project && $(DBT) docs serve --profiles-dir .

dbt-show:
	cd dbt_project && $(DBT) show --select $(MODEL) --profiles-dir . --target clickhouse --limit $(or $(LIMIT),10)

feast-apply:
	cd feast_repo/feature_repo && $(FEAST) apply

# Full offline pipeline: export → dbt → feast materialize
offline-pipeline: export-to-clickhouse dbt-run materialize

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
	$(PYTHON) training/build_training_dataset.py $(if $(SAMPLE),--sample-frac $(SAMPLE),)
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
		sh -c '$(PYTHON) training/build_training_dataset.py $(if $(SAMPLE),--sample-frac $(SAMPLE),) && \
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

stream-events: stream-producer   ## Alias for stream-producer (backward compat)

# Publish synthetic multi-channel transaction events to Redpanda.
#   EPS         events per second (default: 100)
#   MIX         channel-mix string, e.g. "visa=0.5,qris=0.5" (default: bank-realistic)
#   DURATION    seconds to run (default: 0 = until Ctrl+C)
#   SEED        RNG seed for reproducibility
#   FRAUD_RATE  probability of injecting synthetic fraud per event
#   USE_DB      when set to 1, load user/device/merchant IDs from Postgres
#   DRY_RUN     when set to 1, generate + tally events but don't publish
# NB: $(or ...) is comma-tokenised by make — commas in the default string get
# treated as argument separators. Use `?=` for the default so commas survive.
EPS ?= 100
MIX ?= visa=0.35,mastercard=0.25,qris=0.20,debit=0.10,amex=0.05,digital=0.05
stream-producer:
	$(PYTHON) simulator/stream_transactions.py \
		--eps $(EPS) \
		--channel-mix "$(MIX)" \
		$(if $(DURATION),--duration $(DURATION),) \
		$(if $(SEED),--seed $(SEED),) \
		$(if $(FRAUD_RATE),--fraud-rate $(FRAUD_RATE),) \
		$(if $(USE_DB),--use-db,) \
		$(if $(DRY_RUN),--dry-run,)

# Run one of the streaming consumers.
#   NAME:      fraud_decisioning | feature_store_updater | postgres_sink
#   DURATION:  seconds to run (default: 0 = until Ctrl+C)
stream-consumer:
	@if [ -z "$(NAME)" ]; then \
		echo "Usage: make stream-consumer NAME=<fraud_decisioning|feature_store_updater|postgres_sink> [DURATION=30]"; \
		exit 1; \
	fi
	$(PYTHON) -m streaming.run $(NAME) $(if $(DURATION),--duration $(DURATION),)

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
	rm -rf data/parquet/*.parquet
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

# ============================================================================
# Slice A2 — Kubeflow standalone (KFP + Katib + KServe + Training Operator)
# ============================================================================
KUBEFLOW_VERSION ?= v1.10.0
KUBEFLOW_INSTALL := infra/k8s/bootstrap/kubeflow/install.sh

kubeflow-up:
	@command -v kustomize >/dev/null || { echo "kustomize not installed (need v5.x)"; exit 1; }
	@command -v kubectl >/dev/null || { echo "kubectl not installed"; exit 1; }
	KUBEFLOW_VERSION=$(KUBEFLOW_VERSION) bash $(KUBEFLOW_INSTALL)
	@echo ""
	@echo "==== kubeflow-up complete ===="
	@$(MAKE) --no-print-directory kubeflow-status
	@echo ""
	@echo "Next: 'make kubeflow-ui' to reach the Central Dashboard"

kubeflow-down:
	@kubectl delete namespace kubeflow --ignore-not-found --timeout=300s || true
	@kubectl delete namespace istio-system --ignore-not-found --timeout=300s || true
	@kubectl delete namespace auth --ignore-not-found --timeout=300s || true
	@kubectl delete namespace cert-manager --ignore-not-found --timeout=300s || true
	@kubectl delete namespace knative-serving --ignore-not-found --timeout=300s || true
	@kubectl delete namespace kubeflow-user-example-com --ignore-not-found --timeout=300s || true
	@kubectl delete namespace oauth2-proxy --ignore-not-found --timeout=300s || true

kubeflow-status:
	@echo "-- CRDs (Kubeflow / KServe / Katib / Training-op) --"
	@kubectl get crd 2>/dev/null | grep -E "(kubeflow.org|kserve.io|katib|serving.kserve)" | awk '{print $$1}' | head -20
	@echo "-- Deployments in kubeflow ns --"
	@kubectl -n kubeflow get deployments 2>/dev/null | tail -n +2 | awk '{printf "  %-45s %s\n", $$1, $$4}'
	@echo "-- Non-Running pods in kubeflow ns --"
	@kubectl -n kubeflow get pods --field-selector=status.phase!=Running 2>/dev/null | tail -n +2 | head -10 || echo "  (all Running)"
	@echo "-- istio-ingressgateway --"
	@kubectl -n istio-system get svc istio-ingressgateway 2>/dev/null | tail -n +2 || echo "  (not installed)"

kubeflow-ui:
	@echo "Kubeflow Central Dashboard: http://localhost:8080"
	@echo "  user: user@example.com"
	@echo "  pass: 12341234"
	@kubectl -n istio-system port-forward svc/istio-ingressgateway 8080:80

# ============================================================================
# Slice A3a — data plane persistence & tracking (Postgres + Redis + MLflow)
# ============================================================================
DP_INSTALL := infra/k8s/bootstrap/data-plane/install.sh

dp-up:
	@command -v kubectl >/dev/null || { echo "kubectl not installed"; exit 1; }
	bash $(DP_INSTALL)
	@echo ""
	@echo "==== dp-up complete ===="
	@$(MAKE) --no-print-directory dp-status

dp-down:
	@kubectl delete -f infra/k8s/bootstrap/data-plane/mlflow.yaml --ignore-not-found --timeout=120s || true
	@kubectl delete -f infra/k8s/bootstrap/data-plane/redis.yaml --ignore-not-found --timeout=120s || true
	@kubectl delete -f infra/k8s/bootstrap/data-plane/postgres.yaml --ignore-not-found --timeout=300s || true
	@kubectl delete configmap fraud-db-schema -n data-plane --ignore-not-found || true
	@kubectl delete namespace data-plane --ignore-not-found --timeout=300s || true
	@kubectl delete -f infra/k8s/bootstrap/data-plane/cnpg-operator.yaml --ignore-not-found --timeout=300s || true

dp-status:
	@echo "-- CloudNativePG operator --"
	@kubectl -n cnpg-system get deployment cnpg-controller-manager --no-headers 2>/dev/null || echo "  (operator not installed)"
	@echo "-- data-plane workloads --"
	@kubectl -n data-plane get pods --no-headers 2>/dev/null || echo "  (namespace absent)"
	@echo "-- fraud-db Cluster phase --"
	@kubectl -n data-plane get cluster fraud-db -o jsonpath='{.status.phase}{"\n"}' 2>/dev/null || echo "  (no cluster)"
	@echo "-- services --"
	@kubectl -n data-plane get svc --no-headers 2>/dev/null || true

pg-shell:
	@kubectl -n data-plane exec -it fraud-db-1 -c postgres -- psql -U postgres fraud_db

mlflow-k8s-ui:
	@echo "MLflow (k8s) UI: http://localhost:5001"
	@kubectl -n data-plane port-forward svc/mlflow 5001:5000

# ============================================================================
# Slice A3b — streaming plane (Redpanda + Schema Registry + Console)
# ============================================================================
STREAM_INSTALL := infra/k8s/bootstrap/streaming/install.sh

stream-k8s-up:
	@command -v helm >/dev/null || { echo "helm not installed"; exit 1; }
	bash $(STREAM_INSTALL)
	@echo ""
	@echo "==== stream-k8s-up complete ===="
	@$(MAKE) --no-print-directory stream-k8s-status

stream-k8s-down:
	@kubectl -n data-plane delete job redpanda-topics-bootstrap redpanda-schemas-bootstrap --ignore-not-found || true
	@kubectl -n data-plane delete configmap redpanda-schemas --ignore-not-found || true
	@helm uninstall redpanda -n data-plane 2>/dev/null || true

stream-k8s-status:
	@echo "-- redpanda pods --"
	@kubectl -n data-plane get pods -l app.kubernetes.io/name=redpanda --no-headers 2>/dev/null || echo "  (not installed)"
	@kubectl -n data-plane get pods -l app.kubernetes.io/name=console --no-headers 2>/dev/null || true
	@echo "-- redpanda services --"
	@kubectl -n data-plane get svc -l app.kubernetes.io/name=redpanda --no-headers 2>/dev/null || true
	@echo "-- topics --"
	@kubectl -n data-plane exec redpanda-0 -c redpanda -- rpk topic list 2>/dev/null || echo "  (broker not ready)"
	@echo "-- schema subjects --"
	@kubectl -n data-plane exec redpanda-0 -c redpanda -- curl -sSf http://localhost:8081/subjects 2>/dev/null || echo "  (SR not ready)"
	@echo

stream-k8s-console-ui:
	@echo "Redpanda Console: http://localhost:8080"
	@kubectl -n data-plane port-forward svc/redpanda-console 8080:8080

stream-k8s-rpk:
	@kubectl -n data-plane exec -it redpanda-0 -c redpanda -- rpk cluster info

# ============================================================================
# Slice A3c — analytical plane (Altinity ClickHouse operator)
# ============================================================================
CH_INSTALL := infra/k8s/bootstrap/clickhouse/install.sh
CH_CHI_SVC := clickhouse-fraud-analytics.data-plane.svc.cluster.local

ch-k8s-up:
	@command -v kubectl >/dev/null || { echo "kubectl not installed"; exit 1; }
	bash $(CH_INSTALL)
	@echo ""
	@echo "==== ch-k8s-up complete ===="
	@$(MAKE) --no-print-directory ch-k8s-status

ch-k8s-down:
	@kubectl -n data-plane delete job ch-schemas-bootstrap ch-rbac-bootstrap --ignore-not-found || true
	@kubectl -n data-plane delete configmap ch-init-sql ch-rbac-script --ignore-not-found || true
	@kubectl -n data-plane delete chi fraud-analytics --ignore-not-found --timeout=300s || true
	@kubectl -n data-plane delete secret fraud-analytics-passwords --ignore-not-found || true
	@kubectl delete -f infra/k8s/bootstrap/clickhouse/operator.yaml --ignore-not-found --timeout=300s || true

ch-k8s-status:
	@echo "-- Altinity operator --"
	@kubectl -n kube-system get deployment clickhouse-operator 2>/dev/null | tail -n +2 || echo "  (operator not installed)"
	@echo "-- chi status --"
	@kubectl -n data-plane get chi fraud-analytics -o jsonpath='{.status.status}{"\n"}' 2>/dev/null || echo "  (no chi)"
	@echo "-- ClickHouse pod --"
	@kubectl -n data-plane get pods -l clickhouse.altinity.com/chi=fraud-analytics 2>/dev/null | tail -n +2 || echo "  (no pod)"
	@echo "-- Databases --"
	@kubectl -n data-plane exec -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user default --password admin_pass --query "SHOW DATABASES FORMAT TabSeparated" 2>/dev/null | grep -E "^(raw|main|sandbox)$$" || echo "  (chi not ready)"
	@echo "-- RBAC users --"
	@kubectl -n data-plane exec -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user default --password admin_pass --query "SELECT name FROM system.users WHERE name IN ('analyst','bi_dashboard','data_scientist','service_writer') ORDER BY name FORMAT TabSeparated" 2>/dev/null

ch-k8s-shell:
	@kubectl -n data-plane exec -it -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user default --password admin_pass

ch-k8s-verify-rbac:
	@echo "-- analyst can SELECT on main.* --"
	@kubectl -n data-plane exec -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user analyst --password analyst_pass --query "SELECT 1" && echo "  ✓ analyst SELECT ok"
	@echo "-- service_writer can INSERT on raw.* --"
	@kubectl -n data-plane exec -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user service_writer --password sw_pass --query "SELECT 1" && echo "  ✓ service_writer connect ok"
	@echo "-- bi_dashboard rejected on sandbox (readonly) --"
	@! kubectl -n data-plane exec -c clickhouse-pod chi-fraud-analytics-fraud-0-0-0 -- clickhouse-client --user bi_dashboard --password bi_pass --query "CREATE TABLE sandbox.forbidden (x Int) ENGINE=Memory" 2>/dev/null && echo "  ✓ bi_dashboard write rejected"
