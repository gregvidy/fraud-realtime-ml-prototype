# Fraud Realtime ML Prototype

An end-to-end MVP for real-time fraud detection combining offline batch features (Postgres → DuckDB → dbt → Feast) with online sliding-window features (Redis), served via FastAPI with an XGBoost scoring model.

---

## Architecture

```
Synthetic Data Generator
        │
        ▼
  PostgreSQL (raw operational data)
        │
        ▼ scripts/export_pg_to_duckdb.py
  DuckDB (offline analytical store)
        │
        ▼ dbt (--target duckdb)
  Feature tables (fct_*)
        │
        ├──────────────────────────────────┐
        ▼                                  ▼
  Parquet export                  fct_training_dataset
        │                                  │
        ▼ feast apply                Model Training
  Feast (feature registry)               │
        │                          fraud_model.pkl
        ▼ feast materialize               │
  Redis (online feature store)            │
        │                                 │
        └──────────┬──────────────────────┘
                   ▼
             FastAPI /score
                   │
                   ▼
            Fraud Score + Risk Band
```

### Data layer separation
| Layer | System | Responsibility |
|---|---|---|
| Raw operational data | PostgreSQL | Transactions, users, devices, merchants, login events |
| Offline analytical store | DuckDB | Feature engineering, training datasets |
| Feature contract | Feast | Offline + online feature view definitions |
| Online serving | Redis | Sliding-window counters, materialized batch features |

**Guiding principle:** _dbt builds the data. Feast defines the feature. Redis serves the feature._

### Offline pipeline
1. Synthetic data → Postgres raw tables
2. `scripts/export_pg_to_duckdb.py` snapshots raw tables into DuckDB `raw` schema
3. dbt transforms raw tables → `fct_user_features`, `fct_device_features`, `fct_merchant_features`, `fct_training_dataset` (DuckDB `main` schema)
4. `scripts/materialize_features.py` exports DuckDB tables → Parquet, then pushes to Redis via Feast
5. Model trains from `fct_training_dataset` read directly from DuckDB

### Online path (per-transaction)
1. Stream simulator emits transaction events
2. `app/online_features/updater.py` writes sliding-window counters to Redis sorted sets
3. FastAPI retrieves features via Feast online store (Redis) → XGBoost model → fraud score

### Feature versioning
Feature views follow `<entity>_batch_fv_v<N>` naming (e.g. `user_batch_fv_v1`).  
Breaking logic changes increment the version; non-breaking additions do not.  
The active feature service (`fraud_scoring_v1`) pins to a specific set of versioned views — enabling safe incremental upgrades.

---

## Quick Start

### Prerequisites
- Docker + Docker Compose (or Homebrew services)
- Python 3.11+

### 1. Install dependencies
```bash
make setup
cp .env.example .env
```

### 2. Start infrastructure
```bash
make infra-up
```
Starts Postgres (port 5432) and Redis (port 6379).  
Raw tables are created automatically via `sql/bootstrap/01_raw_tables.sql`.

### 3. Generate synthetic data
```bash
make seed-data
```
Generates ~2,000 users, ~4,000 devices, ~300 merchants, and ~180,000 historical transactions (default option) with realistic fraud patterns OR can generate more synthetic data by enabling several parameters.

### 4. Run the full offline pipeline
```bash
make offline-pipeline
```
This is a shortcut that runs steps 5–7 below in sequence. Or run them individually:

### 5. Export Postgres → DuckDB
```bash
make export-to-duckdb
```
Copies all raw tables into the local DuckDB file at `data/duckdb/fraud_offline.duckdb`.

### 6. Run dbt models (on DuckDB)
```bash
make dbt-run
```
Runs staging → intermediate → feature models against DuckDB.  
Creates `fct_user_features`, `fct_device_features`, `fct_merchant_features`, and `fct_training_dataset`.

### 7. Apply Feast definitions and materialize
```bash
make feast-apply
make materialize
```
`feast-apply` registers versioned feature views.  
`materialize` exports DuckDB tables → Parquet, then pushes the last 2 days of feature values into Redis.

### 8. Train the model
```bash
make train
```
Reads `fct_training_dataset` from DuckDB, trains an XGBoost classifier, and saves:
- `models/fraud_model.pkl`
- `models/model_meta.json`

### 9. Start the API
```bash
make start-api
```
FastAPI runs at http://localhost:8000. Swagger UI at http://localhost:8000/docs.

### 10. Stream events (optional)
```bash
make stream-events
```
Simulates 10 events/sec and populates Redis with online features in real time.

### 11. Test scoring
```bash
make score-test
```

---

## Migrating an Existing Deployment

If you have an existing Postgres instance bootstrapped before this refactor, run the DB migration once:

```bash
make migrate-db
```

This adds the `feature_service_version` column to `model_score_log`.

---

## Project Structure

```
fraud-realtime-ml-prototype/
├── docker-compose.yml          Postgres + Redis + API
├── .env.example                Environment variable template
├── Makefile                    All runnable commands
├── requirements.txt            Python dependencies
├── pyproject.toml              Pytest config
│
├── data/
│   └── duckdb/
│       ├── fraud_offline.duckdb   DuckDB offline store (gitignored)
│       └── parquet/               Parquet exports for Feast (gitignored)
│
├── scripts/
│   ├── export_pg_to_duckdb.py  Export Postgres raw tables → DuckDB
│   └── materialize_features.py Export DuckDB features → Parquet → Redis
│
├── sql/
│   ├── bootstrap/
│   │   └── 01_raw_tables.sql   DDL for all Postgres raw tables
│   └── migrations/
│       └── 02_add_feature_service_version.sql
│
├── simulator/
│   ├── generate_reference_data.py
│   ├── generate_historical_transactions.py
│   └── stream_transactions.py
│
├── data_contracts/             Schema documentation per table
│
├── dbt_project/
│   ├── dbt_project.yml         Default target: duckdb
│   ├── profiles.yml            duckdb (primary) + dev/postgres (legacy)
│   └── models/
│       ├── sources.yml         Sources in DuckDB raw schema
│       ├── staging/            stg_* (cleaning only)
│       ├── intermediate/       int_* (rolling window stats)
│       └── features/           fct_* (final feature tables + training set)
│
├── feast_repo/
│   └── feature_repo/
│       ├── feature_store.yaml  offline_store: file (Parquet)
│       ├── entities.py
│       ├── data_sources.py     FileSource → data/duckdb/parquet/
│       ├── feature_views.py    Versioned views: *_fv_v1
│       └── feature_services.py fraud_scoring_v1
│
├── app/
│   ├── main.py                 FastAPI app
│   ├── schemas.py              Request/response schemas
│   ├── feature_fetcher.py      Feast online + Redis feature retrieval
│   ├── scoring.py              Score assembly + feature_service_version logging
│   ├── score_logger.py         Inference log → Postgres model_score_log
│   ├── model_loader.py         Model loading/caching
│   ├── Dockerfile
│   └── online_features/
│       ├── redis_keys.py       Key patterns + TTL constants
│       ├── updater.py          Write sliding-window counters to Redis
│       └── retriever.py        Read online features from Redis
│
├── training/
│   ├── feature_contract.yaml   Feature definitions (source + type)
│   ├── build_training_dataset.py  Read fct_training_dataset from DuckDB
│   ├── train_model.py          XGBoost training
│   └── evaluate_model.py       Evaluation metrics
│
├── models/                     Trained model artifacts (gitignored)
│   ├── fraud_model.pkl
│   └── model_meta.json
│
└── tests/
    ├── test_online_features.py  Unit tests (mocked Redis)
    ├── test_scoring_api.py      API tests (mocked model + features)
    └── test_e2e_smoke.py        Integration smoke tests
```

---

## Feature Groups

| Group | Source | Examples |
|---|---|---|
| Request-time | API payload | `txn_amount`, `is_international`, `local_hour` |
| User offline (dbt/DuckDB → Feast) | Parquet → Redis | `user_txn_count_7d`, `user_avg_ticket_30d`, `user_failed_logins_7d` |
| Device offline (dbt/DuckDB → Feast) | Parquet → Redis | `device_distinct_users_30d`, `device_is_shared_flag` |
| Merchant offline (dbt/DuckDB → Feast) | Parquet → Redis | `merchant_fraud_rate_30d`, `merchant_is_high_risk` |
| User online (Redis) | Redis sorted sets | `user_txn_count_5m`, `user_txn_amount_sum_10m`, `user_distinct_merchants_1h` |
| Device online (Redis) | Redis sorted sets | `device_txn_count_5m`, `device_txn_count_10m` |
| Login risk (Redis) | Redis sorted sets | `user_failed_logins_15m` |

---

## Scoring API

**POST /score**

```json
{
  "transaction_id": "txn-abc123",
  "user_id": "u_000001",
  "device_id": "d_0000001",
  "merchant_id": "m_00001",
  "amount": 350.00,
  "currency": "USD",
  "payment_method": "card",
  "country_code": "US",
  "is_international": false,
  "local_hour": 14
}
```

Response:
```json
{
  "transaction_id": "txn-abc123",
  "score": 0.743218,
  "risk_band": "high",
  "is_flagged": true,
  "model_version": "fraud_xgb_v1",
  "feature_sources": {
    "feast_offline": true,
    "redis_online": true,
    "request_time": true
  }
}
```

Risk bands: `low` (0–0.20), `medium` (0.20–0.50), `high` (0.50–0.80), `critical` (0.80+).

Every prediction is logged to `model_score_log` in Postgres with `model_version`, `feature_service_version`, entity IDs, score, and timestamp for full reproducibility.

---

## Running Tests

```bash
# Unit tests only (no live services needed)
pytest tests/test_online_features.py tests/test_scoring_api.py -v

# E2E smoke tests (requires running stack)
pytest tests/test_e2e_smoke.py -v

# All tests
pytest
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Postgres host |
| `POSTGRES_PORT` | `5432` | Postgres port |
| `POSTGRES_USER` | `fraud_user` | Postgres user |
| `POSTGRES_PASSWORD` | `fraud_pass` | Postgres password |
| `POSTGRES_DB` | `fraud_db` | Database name |
| `DUCKDB_PATH` | `data/duckdb/fraud_offline.duckdb` | DuckDB offline store path |
| `PARQUET_DIR` | `data/duckdb/parquet` | Parquet export directory (Feast offline source) |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `MODEL_PATH` | `models/fraud_model.pkl` | Model artifact path |
| `FEAST_REPO_PATH` | `feast_repo/feature_repo` | Feast repo path |
| `SIM_EVENTS_PER_SECOND` | `10` | Simulator throughput |
| `SIM_FRAUD_RATE` | `0.03` | Simulated fraud rate |

---

## Reset Everything

```bash
make infra-reset   # removes Docker volumes (wipes Postgres + Redis data)
make clean         # removes model artifacts, dbt build outputs, DuckDB + Parquet files
make infra-up      # restart fresh
```
