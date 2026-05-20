# Phase 1 — Foundation & Core Platform

> **Duration**: 6-8 weeks
> **Merged from**: Phase 0 (Stabilize Prototype) + Phase 1 (Scale Offline Layer)
> **Goal**: Deployable MVP — containerized, horizontally scalable, production-grade offline storage

---

## 1.1 What This Phase Delivers

By the end of Phase 1, FraudML is:
- Containerized with Docker Compose (all services)
- Behind an API Gateway (Kong/NGINX)
- Scaled to ≥ 2,000 TPS via horizontal scoring replicas
- Connected to ClickHouse (on-prem) or Snowflake (cloud) for offline analytics
- Monitored with Prometheus + Grafana (infra-level)
- Logging via ELK stack (aligned with Predator observability)

---

## 1.2 Architecture After Phase 1

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          PHASE 1 ARCHITECTURE                          │
│                                                                         │
│  ┌─ API Gateway (Kong / NGINX) ──────────────────────────────────────┐ │
│  │  /api/v1/score        → Scoring Service (load-balanced)           │ │
│  │  /api/v1/train        → Training Service                          │ │
│  │  /api/v1/health       → Health aggregator                         │ │
│  │  Rate limiting: 5,000 req/s per client                            │ │
│  │  Auth: API key / JWT (aligned with Predator API Gateway)          │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ Scoring Plane (horizontal) ──────────────────────────────────────┐ │
│  │                                                                    │ │
│  │  ┌─ Scoring-1 ─────┐  ┌─ Scoring-2 ─────┐  ┌─ Scoring-N ─────┐ │ │
│  │  │ FastAPI          │  │ FastAPI          │  │ FastAPI          │ │ │
│  │  │ 4× Uvicorn       │  │ 4× Uvicorn       │  │ 4× Uvicorn       │ │ │
│  │  │ workers          │  │ workers          │  │ workers          │ │ │
│  │  └──────┬───────────┘  └──────┬───────────┘  └──────┬───────────┘ │ │
│  │         │                     │                     │              │ │
│  │         └─────────┬───────────┘─────────────────────┘              │ │
│  │                   │                                                │ │
│  │            ┌──────▼──────┐                                         │ │
│  │            │  Redis 7    │  (shared online store)                  │ │
│  │            │  ├ models:* │                                         │ │
│  │            │  ├ feast:*  │                                         │ │
│  │            │  └ online:* │                                         │ │
│  │            └─────────────┘                                         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ Training Plane ──────────────────────────────────────────────────┐ │
│  │                                                                    │ │
│  │  ┌─ Training Service ──────────┐  ┌─ Offline Pipeline ──────────┐ │ │
│  │  │ FastAPI (1 worker)          │  │                              │ │ │
│  │  │ ├ /train                    │  │ ClickHouse ◄── dbt models   │ │ │
│  │  │ ├ /validate                 │  │     │                        │ │ │
│  │  │ └ /analyse                  │  │     ▼                        │ │ │
│  │  │                             │  │ fct_training_dataset          │ │ │
│  │  │ MLflow logging              │  │     │                        │ │ │
│  │  └─────────────────────────────┘  │     ▼                        │ │ │
│  │                                    │ Parquet → Feast → Redis     │ │ │
│  │                                    └─────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ Observability ───────────────────────────────────────────────────┐ │
│  │  Prometheus ──► Grafana (dashboards)                              │ │
│  │  Logstash ──► Elasticsearch ──► Kibana (logs)                     │ │
│  │  (Aligned with Predator ELK stack)                                │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1.3 Work Breakdown

### 1.3.1 Containerization & Orchestration

| Task | Description | Days |
|------|-------------|------|
| **Dockerize all services** | Scoring, Training, Redis, PostgreSQL → multi-stage Dockerfiles. Python 3.11+ base image. Pin all dependencies. | 3 |
| **Docker Compose (dev/staging)** | Full stack: scoring (N replicas), training, redis, postgres, clickhouse, mlflow, prometheus, grafana, elasticsearch, kibana, logstash | 3 |
| **API Gateway** | Kong or NGINX as entry point. Route `/score` → scoring pool, `/train` → training. Rate limiting, API key auth, request logging. | 3 |
| **Service health checks** | Each service exposes `/health` with component status (Redis connected, model loaded, DB connected). Gateway aggregates. | 1 |
| **Environment config** | `.env` files for dev/staging/prod. Secrets via Docker secrets or env vars (no hardcoded keys). | 1 |

**Subtotal: ~11 days**

#### Docker Compose Skeleton

```yaml
# docker-compose.yml
version: "3.9"

services:
  # --- API Gateway ---
  gateway:
    image: kong:3.6
    ports:
      - "8000:8000"    # proxy
      - "8001:8001"    # admin
    depends_on:
      - scoring
      - training
    volumes:
      - ./infra/kong/kong.yml:/usr/local/kong/declarative/kong.yml

  # --- Scoring (horizontally scalable) ---
  scoring:
    build:
      context: .
      dockerfile: docker/scoring.Dockerfile
    command: >
      gunicorn serving.app:app
      -w 4 -k uvicorn.workers.UvicornWorker
      --bind 0.0.0.0:8000
      --timeout 30
      --backlog 2048
    environment:
      - REDIS_URL=redis://redis:6379
      - ML_MODE=scoring
      - OMP_NUM_THREADS=1
      - MKL_NUM_THREADS=1
    deploy:
      replicas: 3        # 3 × 4 workers = 12 concurrent scorers
      resources:
        limits:
          cpus: "4"
          memory: 4G
    depends_on:
      - redis

  # --- Training ---
  training:
    build:
      context: .
      dockerfile: docker/training.Dockerfile
    command: >
      gunicorn training.app:app
      -w 1 -k uvicorn.workers.UvicornWorker
      --bind 0.0.0.0:8000
      --timeout 3600
    environment:
      - REDIS_URL=redis://redis:6379
      - CLICKHOUSE_URL=clickhouse://clickhouse:9000/fraudml
      - MLFLOW_TRACKING_URI=http://mlflow:5000
      - ML_MODE=training
    deploy:
      resources:
        limits:
          cpus: "8"
          memory: 16G
    depends_on:
      - redis
      - clickhouse
      - mlflow

  # --- Data Stores ---
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: fraudml
      POSTGRES_USER: fraudml
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    volumes:
      - pg_data:/var/lib/postgresql/data
    secrets:
      - pg_password

  clickhouse:
    image: clickhouse/clickhouse-server:24.3
    ports:
      - "8123:8123"   # HTTP
      - "9000:9000"   # Native
    volumes:
      - ch_data:/var/lib/clickhouse
      - ./infra/clickhouse/init.sql:/docker-entrypoint-initdb.d/init.sql

  # --- ML Infrastructure ---
  mlflow:
    image: ghcr.io/mlflow/mlflow:2.14.0
    command: >
      mlflow server
      --backend-store-uri postgresql://fraudml:${PG_PASSWORD}@postgres:5432/fraudml
      --default-artifact-root /mlflow/artifacts
      --host 0.0.0.0
    ports:
      - "5000:5000"
    volumes:
      - mlflow_artifacts:/mlflow/artifacts
    depends_on:
      - postgres

  # --- Observability ---
  prometheus:
    image: prom/prometheus:v2.51.0
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:11.0.0
    ports:
      - "3000:3000"
    volumes:
      - ./infra/grafana/dashboards:/var/lib/grafana/dashboards
      - ./infra/grafana/provisioning:/etc/grafana/provisioning
    depends_on:
      - prometheus

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
    volumes:
      - es_data:/usr/share/elasticsearch/data
    ports:
      - "9200:9200"

  kibana:
    image: docker.elastic.co/kibana/kibana:8.13.0
    ports:
      - "5601:5601"
    depends_on:
      - elasticsearch

  logstash:
    image: docker.elastic.co/logstash/logstash:8.13.0
    volumes:
      - ./infra/logstash/pipeline:/usr/share/logstash/pipeline
    depends_on:
      - elasticsearch

  # --- Load Testing ---
  locust:
    build:
      context: .
      dockerfile: docker/locust.Dockerfile
    ports:
      - "8089:8089"
    environment:
      - LOCUST_HOST=http://gateway:8000

volumes:
  redis_data:
  pg_data:
  ch_data:
  es_data:
  mlflow_artifacts:

secrets:
  pg_password:
    file: ./secrets/pg_password.txt
```

### 1.3.2 Horizontal Scaling for 2,000 TPS

| Task | Description | Days |
|------|-------------|------|
| **Scoring replica scaling** | Docker Compose `deploy.replicas: N`. Kong upstream load balancing (round-robin). Target: 3 replicas × 4 workers × ~170 RPS each = ~2,000 TPS. | 2 |
| **Redis connection pooling** | Shared `redis.asyncio.ConnectionPool(max_connections=100)` per worker. Test under 2K concurrent connections. | 1 |
| **Model preloading** | On startup: load champion model from Redis/file into memory. No cold-start penalty. Each replica loads independently. | 1 |
| **Graceful shutdown** | Handle `SIGTERM` — drain in-flight requests before stopping. Gunicorn `--graceful-timeout 10`. | 0.5 |
| **Connection pool tuning** | PostgreSQL: `asyncpg pool min=2, max=10` per worker. Redis: `max_connections=50` shared. | 0.5 |

**Subtotal: ~5 days**

#### Scaling Math

```
Target: 2,000 TPS sustained

Per scoring instance:
  4 Uvicorn workers × ~170 concurrent req/s per worker = ~680 RPS

Instances needed:
  2,000 / 680 ≈ 3 instances

Safety margin (30%):
  3 × 1.3 ≈ 4 instances

Resource per instance:
  4 CPU cores, 4 GB RAM

Total scoring resources:
  16 CPU cores, 16 GB RAM
```

### 1.3.3 Offline Storage Migration (DuckDB → ClickHouse)

| Task | Description | Days |
|------|-------------|------|
| **ClickHouse schema design** | Create tables matching current DuckDB raw schema: `raw_transactions`, `raw_users`, `raw_devices`, `raw_merchants`, `raw_login_events`, `fraud_labels`. Use `MergeTree` engine with date partitioning. | 2 |
| **Data export pipeline** | `export_to_clickhouse.py`: DataHub (source DB) → ClickHouse via `clickhouse-connect` Python driver. Incremental: track last exported ID/timestamp. | 3 |
| **dbt adapter migration** | Switch from `dbt-duckdb` to `dbt-clickhouse`. Migrate all 17 models (6 staging + 7 intermediate + 4 feature). Test SQL compatibility — ClickHouse has different window function syntax. | 5-7 |
| **Benchmark: 10M rows** | Load 10M synthetic transactions into ClickHouse. Run full dbt pipeline. Compare: build time, resource usage, query correctness. | 2 |
| **Feast materialization** | ClickHouse → Parquet → Feast → Redis. Same flow as DuckDB but reading from ClickHouse views. | 2 |

**Subtotal: ~14-16 days**

#### ClickHouse Schema Example

```sql
-- ClickHouse table for raw transactions
CREATE TABLE raw_transactions (
    transaction_id   String,
    user_id          String,
    device_id        String,
    merchant_id      String,
    amount           Float64,
    currency         LowCardinality(String),
    payment_method   LowCardinality(String),
    country_code     LowCardinality(String),
    is_international UInt8,
    local_hour       UInt8,
    event_timestamp  DateTime64(3),
    created_at       DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp)
SETTINGS index_granularity = 8192;

-- Materialized view for user transaction stats (replaces dbt intermediate model)
CREATE MATERIALIZED VIEW mv_user_txn_stats_7d
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp)
AS SELECT
    user_id,
    event_timestamp,
    countState() AS txn_count_7d,
    sumState(amount) AS txn_amount_7d,
    uniqState(merchant_id) AS distinct_merchants_7d
FROM raw_transactions
GROUP BY user_id, event_timestamp;
```

#### dbt Migration Notes

| dbt Feature | DuckDB | ClickHouse | Action Required |
|-------------|--------|-----------|-----------------|
| Window functions (RANGE frames) | ✅ Full support | ⚠️ Limited RANGE support | Rewrite with `arrayJoin` + subquery or ClickHouse window functions (added in v21.1) |
| INTERVAL arithmetic | `INTERVAL '7 days'` | `INTERVAL 7 DAY` | Syntax change in dbt models |
| Point-in-time joins | Self-join with window | `asof JOIN` (native) | ClickHouse `asof JOIN` is faster and cleaner |
| Incremental models | `dbt-duckdb` incremental | `dbt-clickhouse` incremental via `inserts_only` | Configure strategy in `dbt_project.yml` |
| Testing | `dbt test` works | `dbt test` works with adapter | No change |

**Cloud alternative**: For cloud deployments, replace ClickHouse with Snowflake. dbt adapter swap is the only change (`dbt-snowflake`). Same SQL models, different adapter config in `profiles.yml`.

### 1.3.4 Observability (Prometheus + Grafana + ELK)

| Task | Description | Days |
|------|-------------|------|
| **Prometheus metrics export** | Add `prometheus_fastapi_instrumentator` to scoring service. Exports: request count, latency histograms (P50/P95/P99), active connections, error rates. | 1 |
| **Custom ML metrics** | Export: model version, cache hit rate, feature fetch latency, inference latency, score distribution buckets. | 2 |
| **Grafana dashboards** | Pre-built dashboards: (1) Scoring Performance (TPS, latency, errors), (2) Redis (memory, connections, hit rate), (3) Training (job duration, model metrics), (4) System (CPU, RAM, disk). | 2 |
| **ELK integration** | Structured JSON logging from all services → Logstash → Elasticsearch → Kibana. Aligned with Predator ELK stack. | 2 |
| **Alerting rules** | Prometheus alerts: P95 > 100ms, error rate > 1%, Redis memory > 80%, training failure. Grafana → webhook/email. | 1 |

**Subtotal: ~8 days**

#### Prometheus Metrics (Scoring Service)

```python
# serving/metrics.py
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Histogram, Counter, Gauge

# Auto-instrumented by Instrumentator
# - http_request_duration_seconds
# - http_requests_total

# Custom ML metrics
FEATURE_FETCH_LATENCY = Histogram(
    "fraudml_feature_fetch_seconds",
    "Feature fetch latency",
    ["source"],  # "redis_feast", "redis_online", "cache_hit"
    buckets=[0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1]
)

INFERENCE_LATENCY = Histogram(
    "fraudml_inference_seconds",
    "Model inference latency",
    buckets=[0.0005, 0.001, 0.002, 0.005, 0.01]
)

SCORE_DISTRIBUTION = Histogram(
    "fraudml_score_value",
    "Distribution of fraud scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

MODEL_VERSION = Gauge(
    "fraudml_model_version_info",
    "Currently loaded model version",
    ["model_name", "model_version"]
)

CACHE_HIT = Counter(
    "fraudml_cache_hits_total",
    "Feature cache hits",
    ["entity_type"]  # "user", "device", "merchant"
)
```

---

## 1.4 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | All services containerized (Docker) | `docker compose up` starts full stack |
| 2 | API Gateway routing + rate limiting | `curl http://gateway:8000/api/v1/score` returns score |
| 3 | Scoring horizontally scaled to ≥ 2,000 TPS | Locust test: 2K users, 0% failures, P50 < 20ms |
| 4 | ClickHouse offline store operational | dbt pipeline runs on 10M rows in < 5 minutes |
| 5 | Feast materialization from ClickHouse | Features in Redis match ClickHouse source values |
| 6 | Prometheus + Grafana dashboards live | Real-time TPS, latency, and system metrics visible |
| 7 | ELK logging operational | Structured logs searchable in Kibana |
| 8 | Health endpoints on all services | `/health` returns component status JSON |

---

## 1.5 Demo Checkpoint (D1)

### What to Show

1. **`docker compose up`** — Full stack starts in < 2 minutes
2. **Locust load test at 2,000 TPS** — Grafana dashboard shows stable latency
3. **dbt pipeline on ClickHouse** — 10M row feature build completes
4. **Grafana dashboard walkthrough** — TPS, latency percentiles, Redis metrics
5. **Kibana log search** — Filter by transaction_id, trace a single request

### Benchmark Targets

| Metric | Target | How to Measure |
|--------|--------|---------------|
| TPS | ≥ 2,000 sustained (60s) | Locust: 2,500 users, ramp 100/s |
| P50 latency | < 20ms | Locust statistics tab |
| P99 latency | < 100ms | Locust statistics tab |
| Error rate | 0% | Locust failures tab |
| dbt build (10M rows) | < 5 minutes | `time make dbt-run` on ClickHouse |
| Stack startup | < 2 minutes | `time docker compose up -d` |

---

## 1.6 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ClickHouse window function syntax differs from DuckDB | dbt model migration takes longer | Prototype 3 most complex models first; consider ClickHouse `asof JOIN` as replacement |
| Redis becomes bottleneck at 2K TPS | Scoring latency increases | Redis Cluster mode (Phase 5) or connection pool tuning |
| Kong adds latency overhead | P50 increases by 1-3ms | Acceptable trade-off; test with and without gateway |
| Docker networking overhead on single host | Limits TPS ceiling | Test with `host` network mode as fallback |
| ELK stack resource-heavy | Competes with scoring for CPU/RAM | Deploy ELK on separate host or limit ES heap to 1GB |
