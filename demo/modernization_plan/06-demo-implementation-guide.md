# Demo Implementation Guide — FraudML Platform

> **Purpose**: Concrete implementation plan for building a working POC that validates all 5 phases.
> This guide tells engineers what to build, in what order, and how to prove each capability works.

---

## Demo Strategy

Each phase builds on the previous one. The demo is **cumulative** — by the final demo (D5), the full platform is running end-to-end.

```
D1 (Phase 1): "It scales"          — 2K TPS, ClickHouse, Grafana
D2 (Phase 2): "It streams"         — CDC → Kafka → ksqlDB → Redis + ScyllaDB
D3 (Phase 3): "It learns at scale" — 100M training, feature registry, multi-model
D4 (Phase 4): "It self-manages"    — Canary, champion/challenger, drift alerts
D5 (Phase 5): "It's production"    — SLA validated, security, CI/CD, resilience
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
├── docker-compose.yml              # Phase 1: core services
├── docker-compose.streaming.yml    # Phase 2: Kafka + ksqlDB + ScyllaDB
├── docker-compose.full.yml         # Phase 3+: full stack
├── docker-compose.prod.yml         # Phase 5: production-ready
│
├── serving/                         # Scoring service
│   ├── app.py                       # FastAPI application
│   ├── scoring.py                   # Score endpoint
│   ├── feature_service.py           # Dual-store feature fetch (Redis + ScyllaDB)
│   ├── feast_direct.py              # Direct Redis Feast reader
│   ├── online_features.py           # Sorted set / ksqlDB feature reader
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
│   ├── validator.py                 # Validate against dbt models + ksqlDB
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
├── streaming/                       # Streaming pipeline configs (Phase 2)
│   ├── debezium/
│   │   ├── source-db-connector.json
│   │   └── README.md
│   ├── ksqldb/
│   │   ├── create_streams.sql
│   │   ├── velocity_aggregates.sql
│   │   └── sink_connectors.sql
│   └── kafka/
│       └── topics.sh
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
│   │   │   ├── streaming-pipeline.json
│   │   │   └── sla-compliance.json
│   │   └── provisioning/
│   ├── logstash/pipeline/
│   ├── clickhouse/init.sql
│   └── scylladb/init.cql
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
│   │   ├── test_cdc_pipeline.py
│   │   └── test_dual_store.py
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

# --- Phase 2: Streaming ---
.PHONY: streaming-up cdc-deploy ksqldb-deploy streaming-pipeline

streaming-up:                          ## Start streaming infrastructure
	docker compose -f docker-compose.streaming.yml up -d kafka zookeeper schema-registry ksqldb connect scylladb

cdc-deploy:                            ## Deploy Debezium CDC connector
	curl -X POST http://localhost:8083/connectors \
		-H "Content-Type: application/json" \
		-d @streaming/debezium/source-db-connector.json

ksqldb-deploy:                         ## Deploy ksqlDB queries
	cat streaming/ksqldb/create_streams.sql | docker exec -i ksqldb ksql http://localhost:8088
	cat streaming/ksqldb/velocity_aggregates.sql | docker exec -i ksqldb ksql http://localhost:8088

streaming-pipeline: streaming-up cdc-deploy ksqldb-deploy  ## Full streaming pipeline

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

demo-d2: streaming-pipeline                                        ## Demo 2: Streaming (after D1)

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

### Phase 2 Build Checklist

| # | Task | Files to Create/Modify | Test |
|---|------|----------------------|------|
| 1 | Kafka + Zookeeper + Schema Registry | `docker-compose.streaming.yml` | Kafka topics created |
| 2 | Debezium connector config | `streaming/debezium/source-db-connector.json` | CDC events in Kafka |
| 3 | ksqlDB stream definitions | `streaming/ksqldb/create_streams.sql` | Streams visible in ksqlDB |
| 4 | ksqlDB velocity aggregates | `streaming/ksqldb/velocity_aggregates.sql` | Window aggregates computed |
| 5 | Redis sink connector | `streaming/ksqldb/sink_connectors.sql` | Velocity features in Redis |
| 6 | ScyllaDB schema | `infra/scylladb/init.cql` | Tables created |
| 7 | ScyllaDB sink connector | Kafka Connect config | Features in ScyllaDB |
| 8 | Dual-store feature service | `serving/feature_service.py` | Redis hit → fast; Redis miss → ScyllaDB fallback |
| 9 | ScyllaDB batch load | `scripts/materialize_to_scylladb.py` | Batch features in ScyllaDB |
| 10 | Integration test: CDC e2e | `tests/integration/test_cdc_pipeline.py` | INSERT → Kafka → Redis in < 5s |

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
