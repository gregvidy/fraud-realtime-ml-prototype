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
- Backed by **ClickHouse** as the sole offline analytical store (DuckDB fully removed) with 4 RBAC roles (`analyst`, `bi_dashboard`, `data_scientist`, `service_writer`) — supports concurrent training queries, dbt builds, and BI dashboards
- Ingesting transactions through **Redpanda** (Kafka-compatible broker, KRaft mode) with **per-channel topics** (`txn.raw.visa`, `txn.raw.mastercard`, `txn.raw.amex`, `txn.raw.qris`, `txn.raw.debit`, `txn.raw.digital`) and **Avro + Schema Registry** contracts from day one
- Serving a multi-channel simulator producing realistic transaction events (channel-specific amount / international / device-sharing profiles); consumed by **3 Python consumer groups** (`fraud-decisioning`, `feature-store-updater`, `postgres-sink`) plus **ClickHouse Kafka Engine** for server-side analytics ingest and aggregation
- Using **Redis** (single node with AOF persistence in Phase 1, upgrading to Redis Cluster in Phase 2) as the **sole** online hot store — no separate durable feature DB
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
│  ┌─ Streaming Plane (Simulator-driven, per-channel + Avro) ──────────┐ │
│  │                                                                    │ │
│  │  simulator ──► Redpanda ──┬─► fraud-decisioning  ──HTTP──► /score │ │
│  │  (6 channels) (Kafka API, │                                        │ │
│  │                per-channel├─► feature-store-updater ──► Redis SS  │ │
│  │                topics +   │   (velocity sorted sets)                │ │
│  │                Avro/SR)   ├─► postgres-sink ──► raw_transactions   │ │
│  │                           └─► ClickHouse Kafka Engine ──► MVs      │ │
│  │                               (server-side ingest + aggregation,   │ │
│  │                                no Python consumer needed)          │ │
│  │                                                                    │ │
│  │  Schema Registry (Avro)   │  Redpanda Console UI :8080             │ │
│  │                                                                    │ │
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
    # AOF persistence + LRU eviction. Cluster mode is added in Phase 2.
    command: redis-server --appendonly yes --appendfsync everysec --maxmemory 2gb --maxmemory-policy allkeys-lru

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
    image: clickhouse/clickhouse-server:24.8
    ports:
      - "8123:8123"   # HTTP (BI tools, dbt, clickhouse-connect)
      - "9000:9000"   # Native
    ulimits:
      nofile: {soft: 262144, hard: 262144}
    volumes:
      - ch_data:/var/lib/clickhouse
      - ./infra/clickhouse/init.sql:/docker-entrypoint-initdb.d/01-init.sql
      - ./infra/clickhouse/streaming.sql:/docker-entrypoint-initdb.d/02-streaming.sql  # Kafka Engine + MVs
      - ./infra/clickhouse/users.d:/etc/clickhouse-server/users.d                       # 4-role RBAC
    depends_on:
      - redpanda

  # --- Streaming (Redpanda: Kafka API + Schema Registry, single binary) ---
  redpanda:
    image: redpandadata/redpanda:v24.2.7
    command:
      - redpanda start
      - --smp 1
      - --overprovisioned
      - --node-id 0
      - --kafka-addr PLAINTEXT://0.0.0.0:9092
      - --advertise-kafka-addr PLAINTEXT://redpanda:9092
      - --pandaproxy-addr 0.0.0.0:8082
      - --schema-registry-addr 0.0.0.0:8081
      - --rpc-addr redpanda:33145
    ports:
      - "9092:9092"   # Kafka API
      - "8081:8081"   # Schema Registry (Avro)
      - "8082:8082"   # Pandaproxy (HTTP-to-Kafka bridge)
      - "9644:9644"   # Admin API
    volumes:
      - redpanda_data:/var/lib/redpanda/data

  redpanda-console:
    image: redpandadata/console:v2.7.2
    ports:
      - "8080:8080"
    environment:
      KAFKA_BROKERS: redpanda:9092
      KAFKA_SCHEMAREGISTRY_ENABLED: "true"
      KAFKA_SCHEMAREGISTRY_URLS: http://redpanda:8081
    depends_on:
      - redpanda

  # --- Streaming Consumers (Phase 1: 3 Python consumers; ClickHouse ingests itself via Kafka Engine) ---
  fraud-decisioning:
    build: {context: ., dockerfile: docker/streaming.Dockerfile}
    command: python -m streaming.run fraud_decisioning
    environment:
      - REDPANDA_BROKERS=redpanda:9092
      - SCHEMA_REGISTRY_URL=http://redpanda:8081
      - SCORING_API_URL=http://gateway:8000/api/v1/score
    depends_on: [redpanda, scoring, gateway]

  feature-store-updater:
    build: {context: ., dockerfile: docker/streaming.Dockerfile}
    command: python -m streaming.run feature_store_updater
    environment:
      - REDPANDA_BROKERS=redpanda:9092
      - SCHEMA_REGISTRY_URL=http://redpanda:8081
      - REDIS_URL=redis://redis:6379
    depends_on: [redpanda, redis]

  postgres-sink:
    build: {context: ., dockerfile: docker/streaming.Dockerfile}
    command: python -m streaming.run postgres_sink
    environment:
      - REDPANDA_BROKERS=redpanda:9092
      - SCHEMA_REGISTRY_URL=http://redpanda:8081
      - POSTGRES_URL=postgresql://fraudml:${PG_PASSWORD}@postgres:5432/fraudml
    depends_on: [redpanda, postgres]

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
  redpanda_data:

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

### 1.3.3 Offline Storage: Full Replacement of DuckDB with ClickHouse

> **Decision**: DuckDB is fully removed. dbt has a single `clickhouse` target. All training, materialization, analytics, and (in §1.3.5) streaming ingest run against ClickHouse. This gives bank clients the shared, multi-user analytical DB they expect.

| Task | Description | Days |
|------|-------------|------|
| **ClickHouse schema + RBAC** | Create schemas `raw`, `main`, `sandbox`. `MergeTree` with `PARTITION BY toYYYYMM(event_timestamp) ORDER BY (user_id, event_timestamp)`. Set up 4 users/roles: `analyst` (read `main.*`), `bi_dashboard` (read `main.*`, quota-limited), `data_scientist` (read `main.*` + read/write `sandbox.*`), `service_writer` (insert on `raw.*`, insert/select on `main.*` for dbt). Configure `users.d/roles.xml`. | 3 |
| **Postgres → ClickHouse export** | Replace `export_pg_to_duckdb.py` with `export_pg_to_clickhouse.py` using ClickHouse's native `postgresql()` table function: `INSERT INTO raw.raw_transactions SELECT * FROM postgresql(...)`. Single-shot copy, no pandas hop. Incremental mode via `event_timestamp > (SELECT max(event_timestamp) FROM raw.raw_transactions)`. | 2 |
| **dbt adapter swap** | `dbt-duckdb` → `dbt-clickhouse`. **Single `clickhouse` target in `profiles.yml`.** Migrate 17 models (6 staging + 7 intermediate + 4 feature). | 2 |
| **Rolling-window macro rewrite** | Create `macros/rolling_windows.sql` — Jinja macro emitting self-join + `countIf`/`sumIf` patterns to replace DuckDB's `RANGE BETWEEN INTERVAL '7 days' PRECEDING`. Apply to all 7 intermediate `int_*_stats` models. | 3-4 |
| **Materialization refactor** | Update `scripts/materialize_features.py` — `duckdb.connect()` → `clickhouse_connect.get_client()`. Parquet stays as Feast handoff format (no Feast changes). | 1 |
| **Training dataset builder refactor** | Update `training/build_training_dataset.py` — CH query, `USING SAMPLE X%` → CH `SAMPLE 0.3` in FROM clause. | 0.5 |
| **Remove all DuckDB references** | Delete `data/duckdb/`, drop `duckdb` target from `profiles.yml`, remove `duckdb` from `requirements.txt`, update Makefile targets. | 0.5 |
| **Benchmark: 10M rows** | Load 10M synthetic transactions. Run full dbt pipeline. Verify concurrent access from 3 users (`analyst` SELECT, `service_writer` INSERT, dbt run) does not conflict. | 2 |

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
```

#### ClickHouse RBAC — 4 roles (POC baseline)

```xml
<!-- infra/clickhouse/users.d/roles.xml -->
<clickhouse>
  <users>
    <analyst>
      <password_sha256_hex>${ANALYST_PW_SHA256}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>readonly</profile>
      <quota>default</quota>
      <grants><query>GRANT SELECT ON main.*</query></grants>
    </analyst>
    <bi_dashboard>
      <password_sha256_hex>${BI_PW_SHA256}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>readonly</profile>
      <quota>bi_quota</quota>          <!-- rate-limited for dashboards -->
      <grants><query>GRANT SELECT ON main.*</query></grants>
    </bi_dashboard>
    <data_scientist>
      <password_sha256_hex>${DS_PW_SHA256}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>default</profile>
      <grants>
        <query>GRANT SELECT ON main.*</query>
        <query>GRANT SELECT, INSERT, ALTER, CREATE TABLE, DROP ON sandbox.*</query>
      </grants>
    </data_scientist>
    <service_writer>
      <password_sha256_hex>${SW_PW_SHA256}</password_sha256_hex>
      <networks><ip>::/0</ip></networks>
      <profile>default</profile>
      <grants>
        <query>GRANT INSERT, SELECT ON raw.*</query>
        <query>GRANT INSERT, SELECT ON main.*</query>  <!-- for dbt runs -->
      </grants>
    </service_writer>
  </users>
  <quotas>
    <bi_quota>
      <interval><duration>3600</duration><queries>10000</queries><result_rows>1000000000</result_rows></interval>
    </bi_quota>
  </quotas>
</clickhouse>
```

#### Rolling-window macro (replaces DuckDB `RANGE BETWEEN INTERVAL`)

ClickHouse window functions do not support `INTERVAL`-based RANGE frames on `DateTime` columns. All 7 `int_*_stats` models are rewritten to use a self-join pattern, encapsulated in a Jinja macro so model SQL stays readable.

```sql
-- dbt_project/macros/rolling_windows.sql
{% macro user_txn_rolling(windows=['1 day','7 day','30 day']) %}
  {%- set widest = windows[-1] -%}
  SELECT
    t.transaction_id, t.user_id, t.event_timestamp,
    {%- for w in windows %}
    countIf(p.event_timestamp BETWEEN t.event_timestamp - INTERVAL {{ w }}
                                  AND t.event_timestamp - INTERVAL 1 SECOND)
      AS user_txn_count_{{ w | replace(' ', '') }},
    sumIf(p.amount,   p.event_timestamp BETWEEN t.event_timestamp - INTERVAL {{ w }}
                                            AND t.event_timestamp - INTERVAL 1 SECOND)
      AS user_txn_amount_{{ w | replace(' ', '') }}{% if not loop.last %},{% endif %}
    {%- endfor %}
  FROM {{ ref('stg_transactions') }} t
  LEFT JOIN {{ ref('stg_transactions') }} p
    ON t.user_id = p.user_id
   AND p.event_timestamp <  t.event_timestamp
   AND p.event_timestamp >= t.event_timestamp - INTERVAL {{ widest }}
  GROUP BY t.transaction_id, t.user_id, t.event_timestamp
{% endmacro %}
```

Point-in-time correctness is preserved by the join predicate `p.event_timestamp < t.event_timestamp` (excludes the current row).

#### dbt Migration Notes

| dbt Feature | DuckDB | ClickHouse | Action Required |
|-------------|--------|-----------|-----------------|
| Window functions (RANGE frames) | ✅ `RANGE BETWEEN INTERVAL '7 days' PRECEDING` | ❌ Not supported on `DateTime` | **Rewrite via `macros/rolling_windows.sql`** (self-join + `countIf`/`sumIf`) |
| INTERVAL arithmetic | `INTERVAL '7 days'` | `INTERVAL 7 DAY` | Syntax change in dbt models |
| Point-in-time joins | Self-join with window | Self-join with `p.ts < t.ts` predicate | Same pattern, cleaner syntax |
| Incremental models | `dbt-duckdb` incremental | `dbt-clickhouse` incremental via `inserts_only` | Configure strategy in `dbt_project.yml` |
| Sampling | `USING SAMPLE X PERCENT` | `SAMPLE 0.3` in FROM clause | Update `build_training_dataset.py` |
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

### 1.3.5 Streaming Simulation with Redpanda

> **Purpose**: Replace the in-process `stream_transactions.py → update_online_features()` call with a Kafka-API-compatible broker (Redpanda) so the POC mirrors how a real bank streams transactions from multiple channels (Visa, Mastercard, AMEX, QRIS, Debit, Digital) into fraud decisioning, feature computation, and analytics — with replay capability, Avro-enforced schemas, and independent per-consumer offsets.

**Architectural principles (locked in for this phase):**

1. **Redpanda as the sole broker** — Kafka API, KRaft mode, single binary; upgrades to 3-node HA in Phase 2.
2. **Per-channel topics from day one** — `txn.raw.<channel>` naming; consumers subscribe by pattern (`^txn\.raw\..*$`) so new channels are additive.
3. **Avro + Schema Registry from day one** — schemas live in `streaming/schemas/*.avsc`; **BACKWARD** compatibility enforced.
4. **3 Python consumer groups + ClickHouse Kafka Engine** — `fraud-decisioning` (calls FastAPI `/score` over HTTP), `feature-store-updater` (Redis sorted sets), `postgres-sink` (Postgres COPY). Analytics ingest is handled server-side by **ClickHouse Kafka Engine + Materialized Views** — no Python `analytics-sink` consumer.
5. **No Debezium, no ksqlDB, no Kafka Connect, no ScyllaDB.** Source integration in production uses transactional outbox (Phase 2). Streaming aggregations are computed by Redis sorted sets (velocity, at read time) and ClickHouse MVs (analytics, at ingest time).

| Task | Description | Days |
|------|-------------|------|
| **Redpanda container + Console** | Add `redpanda` and `redpanda-console` services (KRaft mode, single node for POC, 9092 broker, 8081 Schema Registry, 8080 Console UI). No ZooKeeper. | 1 |
| **Per-channel topic setup** | Create 6 raw topics: `txn.raw.visa`, `txn.raw.mastercard`, `txn.raw.amex`, `txn.raw.qris`, `txn.raw.debit`, `txn.raw.digital` (each 6 partitions, key=`user_id`, retention=7d). Plus `txn.scored` (12 partitions, retention=30d) and `login.events` (6 partitions, retention=7d). DLQ topics per group. | 1 |
| **Avro schemas** | Define `TxnEvent.avsc`, `ScoredTxnEvent.avsc`, `LoginEvent.avsc` in `streaming/schemas/`. Register with Schema Registry. Enforce BACKWARD compatibility. | 1.5 |
| **Multi-channel producer** | Refactor `simulator/stream_transactions.py`: remove in-process `update_online_features` call; publish to per-channel topic based on `channel`. Add `--channel-mix visa=0.35,mastercard=0.25,qris=0.20,...` with channel-specific quirks (QRIS = IDR/small amounts, AMEX = higher tickets, digital = elevated device sharing). Use `confluent-kafka` Producer + Avro serializer. | 2 |
| **Consumer framework** | `streaming/consumers/base.py` — generic async consumer using `aiokafka` + Avro deserializer + retry + DLQ pattern (`<topic>.dlq`). At-least-once via `enable_auto_commit=False` + manual commit after processing. | 1.5 |
| **`fraud-decisioning` consumer** | Reads all 6 `txn.raw.*` topics → calls FastAPI `POST /score` over HTTP (per your confirmed decision) → publishes result to `txn.scored`. Uses `httpx.AsyncClient` with keep-alive connection pool. | 1 |
| **`feature-store-updater` consumer** | Reads `txn.raw.*` → invokes existing `update_online_features()` logic → updates Redis sorted sets. Replaces the in-process call from the current simulator. | 0.5 |
| **`postgres-sink` consumer** | Reads `txn.raw.*` → batches into Postgres `raw_transactions` (asyncpg COPY protocol). Keeps Postgres as system-of-record. | 1 |
| **ClickHouse Kafka Engine + MVs** | `infra/clickhouse/streaming.sql` — create `raw.txn_kafka` (Kafka Engine reading all 6 `txn.raw.*` topics with `AvroConfluent` + Schema Registry URL), `main.transactions` (MergeTree landing table), and MV `main.mv_transactions_ingest`. **Replaces the Python `analytics-sink` consumer entirely.** | 1.5 |
| **Replay drill** | Makefile target `make stream-replay CONSUMER=<group> FROM=1h` uses `rpk group seek` to demonstrate replay. | 0.5 |
| **Integration test** | `tests/integration/test_streaming.py` — produce N events → assert all 3 Python consumers received them + `main.transactions` row count matches; kill and reset one group, verify replay. | 1 |

**Subtotal: ~12 days**

#### File layout

```
streaming/
├── config.py                    # broker addr, topic map, group names, avro paths
├── schemas/
│   ├── TxnEvent.avsc
│   ├── ScoredTxnEvent.avsc
│   └── LoginEvent.avsc
├── schema_registry.py           # register + fetch schemas by subject
├── producer.py                  # confluent_kafka.Producer + AvroSerializer
├── consumers/
│   ├── base.py                  # aiokafka async loop, manual commit, DLQ
│   ├── fraud_decisioning.py     # → HTTP POST /score → publish txn.scored
│   ├── feature_store_updater.py # → Redis sorted sets
│   └── postgres_sink.py         # → Postgres COPY batched insert
└── run.py                       # python -m streaming.run <consumer_name>

infra/clickhouse/
├── init.sql                     # schemas, MergeTree tables (raw + main)
├── streaming.sql                # Kafka Engine tables + MVs (replaces Python analytics-sink)
└── users.d/roles.xml            # 4-role RBAC
```

#### Per-channel topic strategy

| Topic | Partitions | Retention | Key | Purpose |
|---|---|---|---|---|
| `txn.raw.visa` | 6 | 7d | `user_id` | Visa card transactions |
| `txn.raw.mastercard` | 6 | 7d | `user_id` | Mastercard transactions |
| `txn.raw.amex` | 3 | 7d | `user_id` | AMEX transactions (lower volume) |
| `txn.raw.qris` | 6 | 7d | `user_id` | QRIS Indonesia (IDR, high domestic) |
| `txn.raw.debit` | 3 | 7d | `user_id` | Debit card / PIN |
| `txn.raw.digital` | 6 | 7d | `user_id` | Digital wallet / apps |
| `txn.scored` | 12 | 30d | `user_id` | Fraud-decisioning output |
| `login.events` | 6 | 7d | `user_id` | Login attempts |
| `<topic>.dlq` | 3 | 30d | — | Dead-letter per topic |

All Python consumer groups subscribe to a **pattern** (`^txn\.raw\..*$`) so channels can be added without changing consumer code. Partition key = `user_id` guarantees per-user ordering across all channels — essential for correct velocity features.

#### Bank-channel realism knobs (in the producer)

| Channel | Amount profile | International rate | Device sharing rate | Currency mix |
|---|---|---|---|---|
| visa | lognormal(4.0, 1.2) | 12% | low | USD/EUR/GBP |
| mastercard | lognormal(4.0, 1.2) | 12% | low | USD/EUR/GBP |
| amex | lognormal(5.0, 1.0) — higher ticket | 25% | very low | USD-heavy |
| qris | lognormal(2.8, 0.7) — small | <2% (domestic) | medium | IDR only |
| debit | lognormal(3.5, 0.9) | 3% | low | domestic-heavy |
| digital | lognormal(3.8, 1.1) | 8% | **elevated** (fraud signal) | mixed |

#### ClickHouse Kafka Engine + Materialized View (replaces ksqlDB / Kafka Connect sink)

```sql
-- infra/clickhouse/streaming.sql

-- 1. Kafka Engine table consumes directly from Redpanda (all 6 channel topics)
CREATE TABLE raw.txn_kafka
(
    transaction_id String,
    user_id        String,
    device_id      String,
    merchant_id    String,
    channel        LowCardinality(String),
    amount         Float64,
    currency       LowCardinality(String),
    country_code   LowCardinality(String),
    is_international UInt8,
    event_timestamp DateTime64(3, 'UTC')
) ENGINE = Kafka SETTINGS
    kafka_broker_list       = 'redpanda:9092',
    kafka_topic_list        = 'txn.raw.visa,txn.raw.mastercard,txn.raw.amex,txn.raw.qris,txn.raw.debit,txn.raw.digital',
    kafka_group_name        = 'clickhouse-analytics',
    kafka_format            = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers     = 3;

-- 2. Landing MergeTree table (durable, partitioned, queryable)
CREATE TABLE main.transactions
(
    transaction_id String,
    user_id        String,
    device_id      String,
    merchant_id    String,
    channel        LowCardinality(String),
    amount         Float64,
    currency       LowCardinality(String),
    country_code   LowCardinality(String),
    is_international UInt8,
    event_timestamp DateTime64(3, 'UTC'),
    ingested_at    DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp);

-- 3. Materialized View drains the Kafka Engine table into the landing table
CREATE MATERIALIZED VIEW main.mv_transactions_ingest TO main.transactions AS
SELECT * FROM raw.txn_kafka;

-- 4. (Phase 2) Velocity aggregate MV — continuously maintained sliding windows for analytics/monitoring
--    (Real-time serving still uses Redis sorted sets; this MV is for dashboards + monitoring.)
CREATE MATERIALIZED VIEW main.mv_user_velocity_5m
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start) ORDER BY (user_id, window_start)
AS SELECT
    user_id,
    toStartOfInterval(event_timestamp, INTERVAL 5 MINUTE) AS window_start,
    countState()                        AS txn_count_state,
    sumState(amount)                    AS txn_amount_state,
    uniqExactState(merchant_id)         AS distinct_merchants_state
FROM raw.txn_kafka
GROUP BY user_id, window_start;
```

**Result**: ClickHouse ingests the Redpanda stream, persists to durable storage, and maintains real-time analytics aggregates — all server-side, all in SQL. The Python `analytics-sink` consumer originally planned is not needed.

---

## 1.4 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | All services containerized (Docker) | `docker compose up` starts full stack |
| 2 | API Gateway routing + rate limiting | `curl http://gateway:8000/api/v1/score` returns score |
| 3 | Scoring horizontally scaled to ≥ 2,000 TPS | Locust test: 2K users, 0% failures, P50 < 20ms |
| 4 | ClickHouse **sole** offline store — DuckDB fully removed | `rg -l duckdb --type py` returns 0; dbt runs on 10M rows in < 5 minutes |
| 5 | ClickHouse 4-role RBAC working | 4 users authenticate; `analyst` cannot INSERT; `bi_dashboard` hits quota after N queries; `data_scientist` can write to `sandbox.*` only; `service_writer` can INSERT into `raw.*` |
| 6 | Feast materialization from ClickHouse | Features in Redis match ClickHouse source values |
| 7 | Redpanda broker + Schema Registry + Console operational | 6 per-channel topics visible in Console (`:8080`); 3 Avro subjects registered |
| 8 | Multi-channel producer streaming events | Producer at 200 eps emits proportionally across 6 topics per `--channel-mix` |
| 9 | 3 Python consumer groups processing independently | Each group's lag < 500ms at steady state; `rpk group describe` shows independent offsets |
| 10 | `fraud-decisioning` consumer scores via HTTP | Every `txn.raw.*` message produces a matching `txn.scored` message; end-to-end latency P50 < 100ms |
| 11 | ClickHouse Kafka Engine ingesting | `main.transactions` row count grows in real time; MV `main.mv_transactions_ingest` shows no lag |
| 12 | Replay works | `make stream-replay CONSUMER=feature-store-updater FROM=1h` catches up without disturbing other consumers |
| 13 | Prometheus + Grafana dashboards live | Real-time TPS, latency, consumer lag, system metrics visible |
| 14 | ELK logging operational | Structured logs searchable in Kibana |
| 15 | Health endpoints on all services | `/health` returns component status JSON |

---

## 1.5 Demo Checkpoint (D1)

### What to Show

1. **`docker compose up`** — Full stack starts in < 2 minutes (Redpanda + ClickHouse + Redis + Postgres + scoring + training + 3 consumers + Prometheus/Grafana/ELK)
2. **Multi-channel producer** — start at 200 eps; open Redpanda Console (`:8080`) and show 6 topics filling, Avro-decoded messages, per-partition distribution keyed by `user_id`
3. **3 consumer groups + ClickHouse Kafka Engine running** — `rpk group list` shows 4 groups (`fraud-decisioning`, `feature-store-updater`, `postgres-sink`, `clickhouse-analytics`) all with independent lag; run `SELECT count(*) FROM main.transactions` and watch it climb in real time
4. **Replay drill** — `rpk group seek clickhouse-analytics --to-datetime -1h` → ClickHouse row count catches up while scoring stays live
5. **ClickHouse RBAC demo** — log in as `analyst`, run `SELECT`, then try `INSERT` → denied; log in as `data_scientist`, write to `sandbox.experiments` → allowed
6. **Locust load test at 2,000 TPS** — Grafana dashboard shows stable scoring latency **alongside** streaming ingest at 200 eps (proves the two planes don't fight)
7. **dbt pipeline on ClickHouse** — 10M row feature build completes; run **while** streaming ingest continues (proves multi-user concurrency on the analytical DB)
8. **Grafana dashboard walkthrough** — TPS, scoring latency, consumer lag, Redis metrics, ClickHouse insert rate
9. **Kibana log search** — Filter by `transaction_id`, trace across producer → consumer → scoring → sink

### Benchmark Targets

| Metric | Target | How to Measure |
|--------|--------|---------------|
| TPS | ≥ 2,000 sustained (60s) | Locust: 2,500 users, ramp 100/s |
| P50 latency | < 20ms | Locust statistics tab |
| P99 latency | < 100ms | Locust statistics tab |
| Error rate | 0% | Locust failures tab |
| Streaming end-to-end (producer → `txn.scored`) P50 | < 100ms | trace via `event_timestamp` diff |
| Consumer lag at 200 eps | < 500ms per group | `rpk group describe` |
| ClickHouse ingest lag | < 1s | `SELECT max(ingested_at) - max(event_timestamp) FROM main.transactions` |
| dbt build (10M rows) | < 5 minutes | `time make dbt-run` on ClickHouse |
| Stack startup | < 2 minutes | `time docker compose up -d` |

---

## 1.6 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ClickHouse rolling-window rewrite complexity | 7 intermediate models need macro-based rewrite | Prototype `int_user_txn_stats` first; validate row-level parity vs DuckDB output on a 1M-row sample before migrating remaining 6 |
| Redis single-node in Phase 1 | Restart pause disrupts scoring | AOF `everysec` fsync limits data loss to ≤1s; Phase 2 upgrades to Redis Cluster (3-primary + 3-replica) with replica reads for HA |
| Redis becomes bottleneck at 2K TPS | Scoring latency increases | Redis Cluster mode (Phase 2) or connection pool tuning |
| Redpanda single-node in POC = SPOF | Broker down → all streaming halts | Acceptable for POC; documented 3-node upgrade path (Phase 2, same config with `--seeds`); consumers are idempotent and resume on offset commit |
| Avro schema evolution mistakes | Producer-consumer breakage | Enforce **BACKWARD** compatibility in Schema Registry; add CI check on `streaming/schemas/*.avsc` diffs |
| HTTP hop from `fraud-decisioning` consumer adds latency | Extra ~5-10ms vs in-process | Accepted — matches real bank pattern; use `httpx.AsyncClient` with keep-alive; scoring service uses async concurrency to absorb it |
| ClickHouse Kafka Engine consumer rebalance during upgrade | Brief ingest pause | Use `kafka_num_consumers = 3` so partitions can be rebalanced without dropping to zero; MV catches up on resume |
| Kong adds latency overhead | P50 increases by 1-3ms | Acceptable trade-off; test with and without gateway |
| Docker networking overhead on single host | Limits TPS ceiling | Test with `host` network mode as fallback |
| ELK stack resource-heavy | Competes with scoring for CPU/RAM | Deploy ELK on separate host or limit ES heap to 1GB |
