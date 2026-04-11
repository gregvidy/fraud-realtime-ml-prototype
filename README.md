# Fraud Realtime ML Prototype

An end-to-end MVP for real-time fraud detection combining offline batch features (Postgres + dbt + Feast) with online sliding-window features (Redis), served via FastAPI with an XGBoost scoring model.

---

## Architecture

```
Synthetic Data Generator
        │
        ▼
  Postgres (raw tables)
        │
        ▼
   dbt (batch features)
        │
        ├──────────────────────────────────┐
        ▼                                  ▼
Feast (offline registry)          fct_training_dataset
        │                                  │
        ▼                           Model Training
  Feast materialize                        │
        │                            fraud_model.pkl
        ▼                                  │
  Redis (online store)                     │
        │                                  │
        └──────────┬───────────────────────┘
                   ▼
             FastAPI /score
                   │
                   ▼
            Fraud Score + Risk Band
```

### Offline path
1. Synthetic data → Postgres raw tables
2. dbt transforms raw tables → `fct_user_features`, `fct_device_features`, `fct_merchant_features`, `fct_training_dataset`
3. Feast registers dbt tables as batch feature views
4. Model trains from `fct_training_dataset`
5. Feast materializes features to Redis online store

### Online path (per-transaction)
1. Stream simulator emits transaction events
2. `app/online_features/updater.py` writes sliding-window counters to Redis sorted sets
3. FastAPI retrieves Redis features + Feast online features → XGBoost model → fraud score

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
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
This starts Postgres (port 5432) and Redis (port 6379).
Raw tables are created automatically via `sql/bootstrap/01_raw_tables.sql`.

### 3. Generate synthetic data
```bash
make seed-data
```
Generates ~2,000 users, ~4,000 devices, ~300 merchants, and ~180,000 historical transactions with realistic fraud patterns.

### 4. Run dbt models
```bash
make dbt-run
```
Runs staging → intermediate → feature models. Creates `fct_user_features`, `fct_device_features`, `fct_merchant_features`, and `fct_training_dataset`.

### 5. Apply Feast definitions
```bash
make feast-apply
```
Registers entities and feature views in the local Feast registry.

### 6. Materialize features to Redis
```bash
make materialize
```
Pushes the last 2 days of offline feature values into Redis for online serving.

### 7. Train the model
```bash
make train
```
Pulls `fct_training_dataset`, trains an XGBoost classifier, and saves:
- `models/fraud_model.pkl`
- `models/model_meta.json`

### 8. Start the API
```bash
make start-api
```
FastAPI runs at http://localhost:8000. Swagger UI at http://localhost:8000/docs.

### 9. Stream events (optional)
```bash
make stream-events
```
Simulates 10 events/sec and populates Redis with online features in real time.

### 10. Test scoring
```bash
make score-test
```

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
├── sql/bootstrap/
│   └── 01_raw_tables.sql       DDL for all raw tables
│
├── simulator/
│   ├── generate_reference_data.py      Users, devices, merchants
│   ├── generate_historical_transactions.py  Historical txns + labels
│   └── stream_transactions.py          Real-time event simulator
│
├── data_contracts/             Schema documentation per table
│
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── sources.yml
│       ├── staging/            stg_* views (cleaning only)
│       ├── intermediate/       int_* tables (rolling window stats)
│       └── features/           fct_* tables (final feature tables + training set)
│
├── feast_repo/
│   ├── materialize.py
│   └── feature_repo/
│       ├── feature_store.yaml
│       ├── entities.py
│       ├── data_sources.py
│       ├── feature_views.py
│       └── feature_services.py
│
├── app/
│   ├── main.py                 FastAPI app
│   ├── schemas.py              Request/response schemas
│   ├── feature_fetcher.py      Feast + Redis feature retrieval
│   ├── scoring.py              Score assembly
│   ├── model_loader.py         Model loading/caching
│   ├── Dockerfile
│   └── online_features/
│       ├── redis_keys.py       Key patterns + TTL constants
│       ├── updater.py          Write sliding-window counters to Redis
│       └── retriever.py        Read online features from Redis
│
├── training/
│   ├── feature_contract.yaml   Feature definitions (source + type)
│   ├── build_training_dataset.py  Pull labelled dataset from Postgres
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
|-------|--------|---------|
| Request-time | API payload | `txn_amount`, `is_international`, `local_hour` |
| User offline (dbt → Feast) | Postgres/Redis | `user_txn_count_7d`, `user_avg_ticket_30d`, `user_failed_logins_7d` |
| Device offline (dbt → Feast) | Postgres/Redis | `device_distinct_users_30d`, `device_is_shared_flag` |
| Merchant offline (dbt → Feast) | Postgres/Redis | `merchant_fraud_rate_30d`, `merchant_is_high_risk` |
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
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | Postgres host |
| `POSTGRES_PORT` | `5432` | Postgres port |
| `POSTGRES_USER` | `fraud_user` | Postgres user |
| `POSTGRES_PASSWORD` | `fraud_pass` | Postgres password |
| `POSTGRES_DB` | `fraud_db` | Database name |
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
make clean         # removes model artifacts and dbt build outputs
make infra-up      # restart fresh
```
