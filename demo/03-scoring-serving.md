# Scoring & Serving — Fraud Real-Time ML Prototype

## Overview

The scoring service is a **FastAPI application** served by Gunicorn with 4 Uvicorn async workers. It processes `POST /score` requests in **< 50ms end-to-end**, achieving **500+ RPS** on a single server.

Every component in the request path is optimized for low latency: async I/O, parallel feature fetches, pipelined Redis reads, thread-pooled model inference, and non-blocking score logging.

---

## Request Lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gunicorn<br/>(4 Workers)
    participant S as scoring.py
    participant RF as Redis<br/>(Feast Keys)
    participant RO as Redis<br/>(Sorted Sets)
    participant M as LightGBM<br/>(ThreadPool)
    participant PG as PostgreSQL<br/>(Async Queue)

    C->>G: POST /score {user_id, device_id, merchant_id, amount, ...}
    G->>S: Route to score_transaction()

    Note over S: Extract request-time features<br/>(amount, is_international, local_hour)

    par Parallel Feature Fetch (~2-4ms)
        S->>RF: fetch_offline_features()<br/>3× HMGET (user + device + merchant)
        RF-->>S: 38 batch features
    and
        S->>RO: fetch_online_features()<br/>11× ZRANGEBYSCORE (pipelined)
        RO-->>S: 14 real-time features
    end

    Note over S: Assemble 41-feature vector<br/>(ordered by model_meta.json)

    S->>M: predict_proba(X) via ThreadPoolExecutor
    M-->>S: raw_probability (~1ms, GIL-free)

    Note over S: np.interp(raw_prob, calib_x, calib_y)<br/>Fast isotonic calibration (~µs)

    Note over S: Apply threshold → risk_band + is_flagged

    par Non-blocking Logging
        S-->>PG: log_score() → async queue → batch INSERT
        S-->>PG: log_features() → async queue → batch INSERT
    end

    S-->>G: ScoreResponse
    G-->>C: 200 OK {score, risk_band, is_flagged, model_version}
```

---

## Latency Breakdown

```mermaid
gantt
    title Scoring Request Timeline (typical ~8-15ms server-side)
    dateFormat X
    axisFormat %L ms

    section Feature Fetch
    Offline features (Redis/Feast)     :0, 3
    Online features (Redis/SortedSets) :0, 3

    section Inference
    LightGBM predict_proba             :3, 4
    Isotonic calibration (np.interp)   :4, 4

    section Response
    Threshold + risk band              :4, 5
    Build response                     :5, 5

    section Async (non-blocking)
    Score logging to Postgres           :5, 8
    Feature logging to Postgres         :5, 8
```

| Stage | Latency | How |
|-------|---------|-----|
| **Feature fetch (offline)** | ~2ms | Direct Redis HMGET, bypasses Feast SDK, per-entity TTL cache |
| **Feature fetch (online)** | ~2ms | 11 ZRANGEBYSCORE commands in 1 Redis pipeline |
| **Model inference** | ~1ms | LightGBM releases GIL, runs in ThreadPoolExecutor |
| **Calibration** | ~0.01ms | Pre-extracted numpy arrays + `np.interp` |
| **Score logging** | 0ms (async) | Non-blocking queue, batch INSERT every 50ms |
| **Total server-side** | **~3-8ms** | Parallel fetches + thread-pooled predict |

---

## API Endpoints

### `POST /score` — Score a Transaction

**Request:**
```json
{
    "transaction_id": "txn_abc123",
    "user_id": "u_000042",
    "device_id": "d_0001234",
    "merchant_id": "m_00150",
    "amount": 1250.00,
    "currency": "USD",
    "payment_method": "credit_card",
    "country_code": "US",
    "is_international": true,
    "local_hour": 14
}
```

**Response:**
```json
{
    "transaction_id": "txn_abc123",
    "score": 0.7234,
    "risk_band": "high",
    "is_flagged": true,
    "model_version": "lgbm_optimized_model",
    "feature_sources": {
        "feast_offline": true,
        "redis_online": true
    }
}
```

### Risk Bands

| Band | Score Range | Action |
|------|------------|--------|
| **Critical** | ≥ 0.80 | Auto-block + alert |
| **High** | 0.50 – 0.79 | Manual review queue |
| **Medium** | 0.20 – 0.49 | Enhanced monitoring |
| **Low** | < 0.20 | Auto-approve |

### `GET /health` — Health Check

```json
{
    "status": "ok",
    "model_loaded": true,
    "redis_connected": true
}
```

---

## Feature Vector Assembly

The model expects exactly **41 features** in a specific order defined by `model_meta.json`:

```mermaid
flowchart LR
    subgraph REQ["Request-Time Features (3)"]
        R1["txn_amount"]
        R2["is_international"]
        R3["local_hour"]
    end

    subgraph OFFLINE["Batch Features via Feast/Redis (24)"]
        direction TB
        U["User Features (15)<br/>account_age, verified, type,<br/>txn_count_1d/7d/30d,<br/>txn_amount_1d/7d/30d,<br/>distinct_merchants_7d/30d,<br/>distinct_devices_30d,<br/>decline_count_7d,<br/>failed_logins_7d/1d"]
        D["Device Features (4)<br/>distinct_users_30d,<br/>txn_count_7d/1d,<br/>is_shared_flag"]
        M["Merchant Features (5)<br/>high_risk, is_online,<br/>txn_count_30d,<br/>avg_ticket_30d,<br/>fraud_rate_30d"]
    end

    subgraph ONLINE["Real-Time Features via Redis (14)"]
        direction TB
        UO["User Online (11)<br/>txn_count_5m/10m/1h,<br/>txn_amount_5m/10m/1h,<br/>distinct_merchants_5m/10m/1h,<br/>failed_logins_15m/1h"]
        DO["Device Online (3)<br/>txn_count_5m/10m/1h"]
    end

    REQ --> VECTOR["Feature Vector<br/>(41 values, ordered)"]
    OFFLINE --> VECTOR
    ONLINE --> VECTOR
    VECTOR --> MODEL["LightGBM<br/>predict_proba()"]

    style REQ fill:#27ae60,color:#fff
    style OFFLINE fill:#2980b9,color:#fff
    style ONLINE fill:#e74c3c,color:#fff
```

---

## Key Optimizations

### 1. Feast SDK Bypass (`feast_direct.py`)
Instead of using Feast's Python SDK for online serving (~15-20ms), we directly read Redis using Feast's key format:
- Serializes entity keys matching Feast's protobuf format
- Uses `mmh3` field hashes matching Feast's `RedisOnlineStore`
- 3 HMGET calls in 1 Redis pipeline → **~2ms total**

### 2. Per-Entity TTL Cache
```
User cache:    10,000 entries, 60s TTL → ~89% hit rate at 300 RPS
Device cache:  20,000 entries, 60s TTL → ~78% hit rate
Merchant cache: 5,000 entries, 60s TTL → ~98% hit rate
```
On cache hits, the Redis call is skipped entirely — sub-microsecond feature retrieval.

### 3. Thread-Pooled Inference
LightGBM's `predict_proba` releases the Python GIL during computation. A `ThreadPoolExecutor(max_workers=8)` allows multiple workers to predict concurrently without blocking the async event loop.

### 4. Extracted Isotonic Calibration
At model load time, the sklearn `CalibratedClassifierCV` is decomposed into numpy arrays (1000-point interpolation grid). At inference, `np.interp()` replaces the full sklearn predict path — **15-40ms → 0.01ms**.

### 5. Non-Blocking Score Logging
Scores and features are logged via async queues that batch-INSERT to Postgres every 50ms or 100 rows. The API response is returned **before** the database write completes.

---

## Serving Infrastructure

```mermaid
flowchart TD
    subgraph SERVER["Server Process"]
        GUNICORN["Gunicorn Master"]
        W1["Uvicorn Worker 1"]
        W2["Uvicorn Worker 2"]
        W3["Uvicorn Worker 3"]
        W4["Uvicorn Worker 4"]
        TP["ThreadPoolExecutor<br/>(8 threads, shared)"]
    end

    subgraph CONNECTIONS["Connection Pools"]
        PG_POOL["asyncpg Pool<br/>min=2, max=10<br/>(per worker)"]
        REDIS_POOL["redis.asyncio Pool<br/>max_connections=50<br/>(shared)"]
    end

    GUNICORN --> W1 & W2 & W3 & W4
    W1 & W2 & W3 & W4 --> TP
    W1 & W2 & W3 & W4 --> PG_POOL
    W1 & W2 & W3 & W4 --> REDIS_POOL
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| Workers | 4 | Matches available CPU cores for serving |
| Worker type | UvicornWorker | Async I/O for concurrent connections |
| Worker connections | 1000 | High concurrency per worker |
| Backlog | 2048 | Queue depth for burst traffic |
| Timeout | 30s | Kills stuck workers |
| OMP/MKL threads | 1 | Prevents LightGBM thread over-subscription |
