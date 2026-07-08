# Demo Implementation Guide — FraudML Platform

> **Purpose**: Concrete implementation plan for building a working POC that validates all 5 phases.
> This guide tells engineers what to build, in what order, and how to prove each capability works.

---

## Demo Strategy

Each phase builds on the previous one. The demo is **cumulative** — by the final demo (D5), the full platform is running end-to-end.

```
D1 (Phase 1): "It scales & streams"  — 2K TPS, ClickHouse (RBAC), Redpanda + 3 Python consumers, Grafana
D2 (Phase 2): "It integrates"        — Transactional outbox, ClickHouse Kafka Engine + MVs, Redis Cluster
D3 (Phase 3): "It learns at scale"   — 100M training, feature registry, multi-model
D4 (Phase 4): "It self-manages"      — Canary, champion/challenger, drift alerts
D5 (Phase 5): "It's production"      — SLA validated, security, CI/CD, resilience
```

---

## Repository Structure

```
fraud-realtime-ml-prototype/
├── docker/
│   ├── scoring.Dockerfile
│   ├── training.Dockerfile
│   ├── locust.Dockerfile
│   └── ray-worker.Dockerfile
│
├── docker-compose.yml              # Phase 1: core services (incl. Redpanda + Console + Schema Registry)
├── docker-compose.streaming.yml    # Phase 2 additions: outbox-relay service (+ optional Debezium Connect for legacy sources)
├── docker-compose.full.yml         # Phase 3+: full stack
├── docker-compose.prod.yml         # Phase 5: production-ready (Redis Cluster 3P+3R, Redpanda 3-node)
│
├── serving/                         # Scoring service
│   ├── app.py                       # FastAPI application
│   ├── scoring.py                   # Score endpoint
│   ├── feature_service.py           # Redis Cluster hot path + ClickHouse cold-fallback (circuit breaker)
│   ├── feast_direct.py              # Direct Redis Feast reader (Cluster-aware)
│   ├── online_features.py           # Redis sorted-set velocity feature reader
│   ├── model_registry.py            # In-memory model registry with hot-reload
│   ├── calibration.py               # Isotonic calibration (extracted numpy)
│   ├── canary.py                    # Canary routing logic (Phase 4)
│   ├── metrics.py                   # Prometheus metrics
│   ├── health.py                    # Health endpoint
│   └── config.py                    # Pydantic settings
│
├── training/
│   ├── app.py                       # Training FastAPI service
│   ├── train_model.py               # Config-driven training entry point
│   ├── train_distributed.py         # Ray-distributed training (Phase 3)
│   ├── calibrate.py                 # Calibration pipeline
│   ├── evaluate.py                  # Metrics computation
│   ├── promote_model.py             # Champion selection + promotion
│   ├── build_training_dataset.py    # Dataset builder from feature registry
│   ├── export_to_clickhouse.py      # DataHub → ClickHouse export
│   ├── experiments/                 # YAML experiment configs
│   │   ├── lgbm_default.yaml
│   │   ├── full_sweep.yaml
│   │   └── quick_test.yaml
│   └── config.py
│
├── feature_definitions/             # Feature registry (Phase 3)
│   ├── entities.yaml
│   ├── user_features.yaml
│   ├── device_features.yaml
│   ├── merchant_features.yaml
│   └── request_features.yaml
│
├── features/                        # Feature registry Python module
│   ├── __init__.py
│   ├── registry.py                  # Load YAML, serve feature metadata
│   ├── validator.py                 # Validate against dbt models + ClickHouse MVs + Avro schemas
│   └── cli.py                       # CLI: fraudml features list/validate/describe
│
├── monitoring/                      # Monitoring service (Phase 4)
│   ├── drift_detector.py            # Evidently integration
│   ├── score_logger.py              # Async score logging
│   ├── monitoring_service.py        # Daily/weekly monitoring jobs
│   └── alerts.py                    # Alert rules + notification
│
├── lifecycle/                       # Model lifecycle (Phase 4)
│   ├── model_states.py              # State machine
│   ├── lifecycle_service.py         # Promote, rollback, canary evaluation
│   └── api.py                       # REST endpoints for model management
│
├── streaming/                       # Redpanda streaming (Phase 1: producer + 3 consumers; Phase 2: outbox-relay + optional Debezium fallback)
│   ├── config.py                    # broker addr, topic map, group names, avro paths
│   ├── schemas/                     # Avro schemas registered in Redpanda Schema Registry
│   │   ├── TxnEvent.avsc
│   │   ├── ScoredTxnEvent.avsc
│   │   └── LoginEvent.avsc
│   ├── schema_registry.py           # register + fetch schemas by subject
│   ├── producer.py                  # confluent_kafka.Producer + AvroSerializer
│   ├── consumers/
│   │   ├── base.py                  # aiokafka async loop, manual commit, DLQ
│   │   ├── fraud_decisioning.py     # → HTTP POST /score → publish txn.scored
│   │   ├── feature_store_updater.py # → Redis Cluster sorted sets
│   │   └── postgres_sink.py         # → Postgres COPY batched insert
│   ├── outbox_relay.py              # Phase 2 — polls outbox_events, publishes to Redpanda
│   ├── run.py                       # python -m streaming.run <consumer_name>
│   ├── rpk/
│   │   └── topics.sh                # rpk topic create bootstrap
│   └── debezium/                    # Phase 2 fallback — used only when source can't be modified
│       ├── source-db-connector.json
│       └── README.md
│
├── dbt_features/                    # dbt project (Phase 1 → ClickHouse)
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── staging/
│       ├── intermediate/
│       └── features/
│
├── infra/                           # Infrastructure configs
│   ├── kong/kong.yml
│   ├── prometheus/prometheus.yml
│   ├── grafana/
│   │   ├── dashboards/
│   │   │   ├── scoring-performance.json
│   │   │   ├── ml-monitoring.json
│   │   │   ├── streaming-pipeline.json    # topic lag, consumer group progress, MV ingest rate
│   │   │   └── sla-compliance.json
│   │   └── provisioning/
│   ├── logstash/pipeline/
│   ├── clickhouse/
│   │   ├── init.sql                       # Schemas raw/main/sandbox + MergeTree tables
│   │   ├── users.d/roles.xml              # 4 RBAC roles (analyst, bi_dashboard, data_scientist, service_writer)
│   │   └── streaming.sql                  # Kafka Engine tables + Materialized Views (Phase 2)
│   └── redpanda/
│       └── console.yaml                   # Redpanda Console + Schema Registry config
│
├── helm/fraudml/                    # Kubernetes Helm chart (Phase 5)
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│
├── tests/
│   ├── unit/
│   │   ├── test_scoring.py
│   │   ├── test_feature_registry.py
│   │   ├── test_calibration.py
│   │   ├── test_model_lifecycle.py
│   │   └── test_drift_detector.py
│   ├── integration/
│   │   ├── test_score_e2e.py
│   │   ├── test_training_e2e.py
│   │   ├── test_streaming.py                # Redpanda producer + 3 consumers e2e + replay
│   │   ├── test_outbox_relay.py             # Outbox → Redpanda, HA duplicate check (Phase 2)
│   │   ├── test_clickhouse_kafka_engine.py  # Kafka Engine + MVs ingesting (Phase 2)
│   │   └── test_cold_fallback.py            # Redis down → ClickHouse fallback path (Phase 2)
│   └── load/
│       ├── locustfile.py
│       └── scenarios/
│           ├── sustained_2k.py
│           ├── burst_5x.py
│           ├── soak_1h.py
│           └── ramp_up.py
│
├── scripts/
│   ├── seed_data.py                 # Generate synthetic data
│   ├── setup_tenants.py             # Create demo tenants
│   ├── materialize_features.py      # Feast materialization
│   └── generate_drift_data.py       # Inject drifted data for monitoring demo
│
├── Makefile                         # All commands
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## Makefile (All Demo Commands)

```makefile
# ============================================================
# FRAUDML PLATFORM — DEMO COMMANDS
# ============================================================

# --- Phase 1: Foundation ---
.PHONY: infra-up infra-down seed-data start-api load-test

infra-up:                              ## Start core infrastructure
	docker compose up -d redis postgres clickhouse mlflow prometheus grafana elasticsearch kibana logstash

infra-down:                            ## Stop all infrastructure
	docker compose down -v

seed-data:                             ## Generate and load synthetic data
	python scripts/seed_data.py --users 100000 --transactions 1000000

start-api:                             ## Start scoring API (3 replicas)
	docker compose up -d --scale scoring=3 scoring

start-training:                        ## Start training service
	docker compose up -d training

load-test:                             ## Run Locust load test (headless)
	docker compose run --rm locust \
		-f /tests/load/locustfile.py \
		--headless -u 2500 -r 100 -t 120s \
		--host http://gateway:8000

load-test-ui:                          ## Start Locust web UI
	docker compose up -d locust

# --- Phase 1: Offline Pipeline ---
.PHONY: export-to-clickhouse dbt-run materialize offline-pipeline

export-to-clickhouse:                  ## Export data to ClickHouse
	python training/export_to_clickhouse.py

dbt-run:                               ## Run dbt feature models on ClickHouse
	cd dbt_features && dbt run --profiles-dir .

materialize:                           ## Materialize features to Redis via Feast
	python scripts/materialize_features.py

offline-pipeline: export-to-clickhouse dbt-run materialize  ## Full offline pipeline

# --- Phase 1: Streaming (Redpanda) ---
.PHONY: stream-topics stream-schemas stream-producer stream-consumer stream-replay stream-status

stream-topics:                         ## Create per-channel Redpanda topics
	bash streaming/rpk/topics.sh

stream-schemas:                        ## Register Avro schemas with Schema Registry
	python -m streaming.schema_registry register

stream-producer:                       ## Start multi-channel producer (EPS + channel mix)
	python simulator/stream_transactions.py \
		--eps $(or $(EPS),200) \
		--channel-mix $(or $(MIX),visa=0.35,mastercard=0.25,qris=0.20,debit=0.10,amex=0.05,digital=0.05)

stream-consumer:                       ## Start a named consumer (NAME=fraud_decisioning|feature_store_updater|postgres_sink)
	python -m streaming.run $(NAME)

stream-replay:                         ## Reset a consumer group to a point in time (CONSUMER=<group> FROM=1h)
	rpk group seek $(CONSUMER) --to-datetime $$(date -u -d "$(FROM) ago" +%Y-%m-%dT%H:%M:%S)

stream-status:                         ## Show topic + consumer group status
	rpk topic list
	rpk group list

# --- Phase 2: Production Integration ---
.PHONY: outbox-relay clickhouse-streaming redis-cluster-up debezium-fallback

outbox-relay:                          ## Start the transactional outbox relay (2 replicas for HA)
	docker compose -f docker-compose.streaming.yml up -d outbox-relay

clickhouse-streaming:                  ## Apply ClickHouse Kafka Engine + Materialized Views
	docker exec -i fraud_clickhouse clickhouse-client --multiquery < infra/clickhouse/streaming.sql

redis-cluster-up:                      ## Upgrade Redis to 3-primary + 3-replica Cluster
	docker compose -f docker-compose.prod.yml up -d redis-node-1 redis-node-2 redis-node-3 redis-node-4 redis-node-5 redis-node-6
	docker exec redis-node-1 redis-cli --cluster create <node1..6-ips>:6379 --cluster-replicas 1 --cluster-yes

debezium-fallback:                     ## (Legacy sources only) Deploy Debezium CDC connector
	curl -X POST http://localhost:8083/connectors \
		-H "Content-Type: application/json" \
		-d @streaming/debezium/source-db-connector.json

# --- Phase 3: Training ---
.PHONY: train train-sweep train-distributed features-validate

train:                                 ## Train single model
	python training/train_model.py --config training/experiments/lgbm_default.yaml

train-sweep:                           ## Multi-model sweep
	python training/train_model.py --config training/experiments/full_sweep.yaml

train-distributed:                     ## Distributed training on Ray
	RAY_ADDRESS=ray://ray-head:10001 python training/train_distributed.py \
		--config training/experiments/full_sweep.yaml

features-validate:                     ## Validate feature registry
	python -m features.cli validate

# --- Phase 4: Lifecycle ---
.PHONY: promote-model rollback-model enable-canary drift-report

promote-model:                         ## Promote model to champion
	curl -X POST http://localhost:8000/api/v1/models/$(MODEL_ID)/promote \
		-H "Content-Type: application/json" \
		-d '{"target_state": "champion"}'

rollback-model:                        ## Rollback to previous champion
	curl -X POST http://localhost:8000/api/v1/models/$(MODEL_ID)/rollback

enable-canary:                         ## Enable canary deployment
	curl -X POST http://localhost:8000/api/v1/tenants/$(TENANT_ID)/canary \
		-H "Content-Type: application/json" \
		-d '{"challenger_model_id": $(MODEL_ID), "percentage": 20}'

drift-report:                          ## Generate drift report
	python monitoring/drift_detector.py --tenant $(TENANT_ID)

inject-drift:                          ## Inject drifted data for demo
	python scripts/generate_drift_data.py --drift-factor 2.0 --duration 24h

# --- Phase 5: Production ---
.PHONY: test test-unit test-integration test-load security-scan

test: test-unit test-integration       ## Run all tests

test-unit:                             ## Unit tests
	pytest tests/unit/ -v --cov

test-integration:                      ## Integration tests (requires infra)
	pytest tests/integration/ -v

test-load:                             ## Full load test suite
	pytest tests/load/scenarios/ -v

security-scan:                         ## Security scanning
	pip-audit
	trivy image fraudml-scoring:latest

# --- Full Demo ---
.PHONY: demo-full demo-d1 demo-d2 demo-d3 demo-d4 demo-d5

demo-d1: infra-up seed-data offline-pipeline start-api load-test  ## Demo 1: Foundation

demo-d2: outbox-relay clickhouse-streaming                         ## Demo 2: Production Integration (after D1)

demo-d3: features-validate train-sweep                             ## Demo 3: Training (after D2)

demo-d4: enable-canary drift-report                                ## Demo 4: Lifecycle (after D3)

demo-d5: test security-scan test-load                              ## Demo 5: Production (after D4)
```

---

## Synthetic Data Generation

```python
# scripts/seed_data.py (simplified)
"""Generate synthetic fraud data for demo."""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def generate_users(n=100_000):
    return pd.DataFrame({
        "user_id": [f"u_{i:06d}" for i in range(n)],
        "account_age_days": np.random.exponential(365, n).astype(int),
        "is_verified": np.random.choice([True, False], n, p=[0.85, 0.15]),
        "account_type": np.random.choice(["personal", "business"], n, p=[0.9, 0.1]),
        "created_at": [datetime.now() - timedelta(days=int(d)) for d in np.random.exponential(365, n)]
    })

def generate_transactions(n=1_000_000, fraud_rate=0.015):
    """
    Generate transactions with realistic fraud patterns:
    - Fraud rate: ~1.5%
    - Fraud transactions: higher amounts, more international, odd hours
    - Non-fraud: normal distribution of amounts and hours
    """
    is_fraud = np.random.choice([0, 1], n, p=[1 - fraud_rate, fraud_rate])
    
    amounts = np.where(
        is_fraud,
        np.random.lognormal(7, 1.5, n),   # Fraud: higher amounts
        np.random.lognormal(4, 1.2, n)     # Normal: lower amounts
    )
    
    hours = np.where(
        is_fraud,
        np.random.choice(range(24), n, p=_odd_hour_dist()),  # Fraud: odd hours
        np.random.choice(range(24), n, p=_normal_hour_dist())
    )
    
    return pd.DataFrame({
        "transaction_id": [f"txn_{i:012d}" for i in range(n)],
        "user_id": np.random.choice([f"u_{i:06d}" for i in range(100_000)], n),
        "device_id": np.random.choice([f"d_{i:07d}" for i in range(50_000)], n),
        "merchant_id": np.random.choice([f"m_{i:05d}" for i in range(10_000)], n),
        "amount": amounts.round(2),
        "currency": "USD",
        "payment_method": np.random.choice(
            ["credit_card", "debit_card", "bank_transfer", "e_wallet"],
            n, p=[0.5, 0.3, 0.1, 0.1]
        ),
        "country_code": np.random.choice(["US", "GB", "SG", "MY", "ID", "TH"], n),
        "is_international": np.where(is_fraud, np.random.random(n) < 0.4, np.random.random(n) < 0.1),
        "local_hour": hours,
        "is_fraud": is_fraud,
        "event_timestamp": pd.date_range(
            end=datetime.now(), periods=n, freq="s"
        ).values
    })

def generate_large_dataset(target_rows=100_000_000, chunk_size=1_000_000):
    """Generate 100M+ rows in chunks, write directly to ClickHouse."""
    import clickhouse_connect
    client = clickhouse_connect.get_client(host="clickhouse", port=8123)
    
    for i in range(0, target_rows, chunk_size):
        chunk = generate_transactions(n=chunk_size)
        client.insert_df("raw_transactions", chunk)
        print(f"  Inserted {i + chunk_size:,} / {target_rows:,} rows")
```

---

## Per-Phase Implementation Checklist

### Phase 1 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Multi-stage Dockerfiles | `docker/scoring.Dockerfile`, `docker/training.Dockerfile` | `docker build` succeeds |
| 2 | Docker Compose (core) | `docker-compose.yml` | `docker compose up` → all healthy |
| 3 | Kong API Gateway config | `infra/kong/kong.yml` | `curl gateway:8000/api/v1/health` → 200 |
| 4 | Scoring replica scaling | `docker-compose.yml` (replicas: 3) | Locust: 2K TPS, 0% errors |
| 5 | ClickHouse schema + init | `infra/clickhouse/init.sql` | Tables created on startup |
| 6 | Data export pipeline | `training/export_to_clickhouse.py` | 1M rows in ClickHouse |
| 7 | dbt → ClickHouse models | `dbt_features/models/**/*.sql`, `profiles.yml` | `dbt run` succeeds |
| 8 | Feast materialization | `scripts/materialize_features.py` | Features in Redis match ClickHouse |
| 9 | Prometheus metrics | `serving/metrics.py` | Grafana shows TPS + latency |
| 10 | Grafana dashboards | `infra/grafana/dashboards/*.json` | 4 dashboards visible |
| 11 | ELK logging | `infra/logstash/pipeline/`, structured JSON logs | Logs searchable in Kibana |
| 12 | Locust load test | `tests/load/locustfile.py` | 2K TPS sustained |
| 13 | ClickHouse 4-role RBAC | `infra/clickhouse/users.d/roles.xml` | `analyst`/`bi_dashboard`/`data_scientist`/`service_writer` grants enforced |
| 14 | Redpanda + Console + Schema Registry | `docker-compose.yml` (redpanda, redpanda-console services) | 6 topics visible in Console; Schema Registry serves 3 subjects |
| 15 | Avro schemas registered | `streaming/schemas/*.avsc`, `streaming/schema_registry.py` | `curl http://localhost:8081/subjects` returns 3 subjects |
| 16 | Multi-channel producer | `simulator/stream_transactions.py`, `streaming/producer.py` | 200 eps distributed across 6 channels per `--channel-mix` |
| 17 | 3 consumer groups | `streaming/consumers/*.py`, `streaming/run.py` | `rpk group list` shows 3 groups, all with lag < 500ms |
| 18 | Fraud-decisioning HTTP scoring | `streaming/consumers/fraud_decisioning.py` | Every `txn.raw.*` produces a `txn.scored` within 100ms P50 |
| 19 | Replay works | `make stream-replay CONSUMER=<group> FROM=1h` | Consumer catches up on 1h of events; other consumers unaffected |
| 20 | DuckDB fully removed | (repo scan) | `rg -l duckdb --type py` returns 0; `dbt-duckdb` not in `requirements.txt` |

### Phase 2 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Outbox schema in Predator DB | Migration in `sql/migrations/` | `outbox_events` table with unpublished index exists |
| 2 | Application-level dual-write | Predator services (or simulated stub for demo) | INSERT into `transactions` + `outbox_events` in one txn |
| 3 | `outbox-relay` service | `streaming/outbox_relay.py`, `docker-compose.streaming.yml` | Event lands in Redpanda within 2s of DB commit |
| 4 | Relay HA — no duplicates | 2 replicas via `docker compose scale outbox-relay=2` | `SELECT COUNT(*), COUNT(DISTINCT id) FROM main.transactions` returns equal counts on 1M-event test |
| 5 | Redpanda 3-node HA upgrade | `docker-compose.prod.yml` (redpanda-1, -2, -3 with `--seeds`) | `rpk cluster health` reports 3 healthy nodes |
| 6 | Debezium fallback path (optional) | `streaming/debezium/source-db-connector.json` + `RegexRouter` SMT | With outbox off, CDC events land in `txn.raw.<channel>` topics |
| 7 | ClickHouse Kafka Engine + landing MVs | `infra/clickhouse/streaming.sql` (`raw.txn_kafka`, `main.mv_transactions_ingest`) | Publish N events → `SELECT count() FROM main.transactions` = N within 2s |
| 8 | ClickHouse velocity MVs | `infra/clickhouse/streaming.sql` (`main.mv_user_velocity_5m/10m/1h/24h`, device variants) | Aggregates match Redis sorted-set counts within 1% |
| 9 | `main.mv_latest_features` MV | `infra/clickhouse/streaming.sql` (ReplacingMergeTree) | Every scored entity has a row |
| 10 | Redis Cluster (3P+3R, AOF) | `docker-compose.prod.yml` | Kill primary → replica promotes; no data loss beyond 1s |
| 11 | Cold-fallback circuit breaker | `serving/feature_service.py` | Stop Redis → scoring continues via ClickHouse (P50 ~50ms) |
| 12 | `analytics-sink` consumer **removed** | Delete `streaming/consumers/analytics_sink.py` if it existed in Phase 1 | Not present; ClickHouse Kafka Engine covers this role |
| 13 | Integration test: outbox e2e | `tests/integration/test_outbox_relay.py` | DB commit → Redpanda in < 2s; no duplicates under HA |
| 14 | Integration test: Kafka Engine | `tests/integration/test_clickhouse_kafka_engine.py` | Events ingested + aggregates correct |
| 15 | Integration test: cold fallback | `tests/integration/test_cold_fallback.py` | Redis down → scoring stays live via CH |

### Phase 3 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Feature YAML definitions | `feature_definitions/*.yaml` | Schema valid |
| 2 | Feature registry module | `features/registry.py`, `features/validator.py` | `features-validate` passes |
| 3 | Feature CLI | `features/cli.py` | `fraudml features list` works |
| 4 | Registry-driven scoring | `serving/scoring.py` (uses registry for feature order) | Score response correct |
| 5 | Registry-driven dataset builder | `training/build_training_dataset.py` | Dataset has 41 columns in correct order |
| 6 | Ray cluster Docker setup | `docker/ray-worker.Dockerfile`, compose config | `ray status` shows workers |
| 7 | Distributed LightGBM training | `training/train_distributed.py` | Trains on 10M rows with Ray |
| 8 | Multi-model sweep | `training/experiments/full_sweep.yaml` | 3 model types trained, MLflow logged |
| 9 | 100M row benchmark | `scripts/seed_data.py` (large mode) | Training completes on 100M rows |
| 10 | Consistency validation | `training/validate_consistency.py` | < 1% training-serving skew |

### Phase 4 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Model state machine | `lifecycle/model_states.py` | State transitions work |
| 2 | Lifecycle service | `lifecycle/lifecycle_service.py` | Promote, rollback, canary eval |
| 3 | Lifecycle API | `lifecycle/api.py` | REST endpoints return correct responses |
| 4 | Canary routing | `serving/canary.py` | Dual scoring on X% of traffic |
| 5 | Score logging (async) | `monitoring/score_logger.py` | `score_log` table populated |
| 6 | Feature logging (sampled) | `monitoring/score_logger.py` | `feature_log` table populated |
| 7 | Evidently drift detector | `monitoring/drift_detector.py` | HTML report generated |
| 8 | Monitoring cron job | `monitoring/monitoring_service.py` | Daily snapshot stored |
| 9 | Grafana ML dashboards | `infra/grafana/dashboards/ml-monitoring.json` | 5 panels visible |
| 10 | Alert rules | Prometheus alerting rules | PSI > 0.25 → notification fires |
| 11 | DB migrations | PostgreSQL migration scripts | All new tables created |

### Phase 5 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Load test suite (4 scenarios) | `tests/load/scenarios/*.py` | All pass targets |
| 2 | Unit tests (>80% coverage) | `tests/unit/*.py` | `pytest --cov` > 80% |
| 3 | Integration tests | `tests/integration/*.py` | All pass |
| 4 | TLS configuration | Docker Compose + certs | HTTPS works |
| 5 | Secrets management | Docker secrets config | No hardcoded passwords |
| 6 | RBAC (API keys) | Kong plugin config | Unauthorized → 401 |
| 7 | CI/CD pipeline | `.github/workflows/ci.yml` | Pipeline green on push |
| 8 | Helm chart | `helm/fraudml/**` | `helm install` succeeds |
| 9 | Runbooks | `docs/runbooks/*.md` | 4 runbooks complete |
| 10 | SLA dashboard | `infra/grafana/dashboards/sla-compliance.json` | Dashboard shows compliance % |

---

## Demo Environment Requirements

### Minimal (Development / Demo D1-D2)

| Resource | Specification |
|----------|--------------|
| CPU | 8 cores |
| RAM | 32 GB |
| Storage | 100 GB SSD |
| OS | Ubuntu 22.04+ / macOS (Docker Desktop) |
| Docker | 24.0+ |
| Docker Compose | 2.20+ |

### Full Stack (Demo D3-D5)

| Resource | Specification |
|----------|--------------|
| CPU | 16+ cores |
| RAM | 64 GB |
| Storage | 500 GB SSD (for 100M row dataset) |
| OS | Ubuntu 22.04+ |
| Docker | 24.0+ |
| Docker Compose | 2.20+ |

### Cloud (K8s Demo)

| Resource | Specification |
|----------|--------------|
| K8s cluster | 3 nodes, each: 8 vCPU, 32 GB RAM |
| Storage | 500 GB per node (EBS/PD) |
| Load balancer | Cloud provider ALB/NLB |

---

## Quick Start (From Zero to Demo D1)

```bash
# 1. Clone and enter project
cd fraud-realtime-ml-prototype

# 2. Start infrastructure
make infra-up
# Wait ~60 seconds for all services to initialize

# 3. Seed synthetic data (1M transactions)
make seed-data

# 4. Run offline feature pipeline
make offline-pipeline
# ClickHouse export → dbt run → Feast materialize → Redis

# 5. Start scoring API (3 replicas behind Kong)
make start-api

# 6. Verify health
curl http://localhost:8000/api/v1/health
# {"status":"ok","model_loaded":true,"redis_connected":true,"replicas":3}

# 7. Score a transaction
curl -s -X POST http://localhost:8000/api/v1/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"demo_001","user_id":"u_000042","device_id":"d_0001234","merchant_id":"m_00150","amount":1250.00,"is_international":true,"local_hour":14}' \
  | python3 -m json.tool

# 8. Open dashboards
# Grafana:  http://localhost:3000  (admin/admin)
# MLflow:   http://localhost:5000
# Kibana:   http://localhost:5601
# Locust:   http://localhost:8089

# 9. Run load test (2K TPS target)
make load-test
# Watch Grafana for real-time TPS and latency
```
