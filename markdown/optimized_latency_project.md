## Plan: 1000–1500 TPS at <100ms avg latency

### Diagnosed bottlenecks (severity order)

Before touching anything, here's what the current critical path looks like per request — and why 1000+ TPS is blocked:

| # | Bottleneck | File | Impact at 1000 TPS |
|---|---|---|---|
| 1 | **Sync Postgres commit on every request** | score_logger.py, feature_logger.py | **Fatal** — 2 blocking `conn.commit()` calls, single shared connection, no pool |
| 2 | **Sync FastAPI endpoint** | main.py | **Fatal** — `def score_endpoint` runs in uvicorn threadpool, limits concurrency |
| 3 | **Sequential Redis round trips** | retriever.py | **Critical** — 6–8 separate `ZRANGEBYSCORE` calls executed one-by-one per request |
| 4 | **Feast SDK call** | feature_fetcher.py | **High** — `get_online_features()` for 30+ feature keys goes through Feast's SDK stack |
| 5 | **Single Redis client, no explicit pool** | retriever.py | **High** — default pool is too small for 1000 concurrent requests |
| 6 | **Sequential Feast → Redis fetches** | scoring.py | **Medium** — called one after the other, could be parallel |
| 7 | **Single uvicorn worker** | Makefile `start-api` | **Medium** — single process, not using all CPU cores |

---

### Phase 1 — Decouple DB logging from the hot path *(biggest single gain)*

**The problem:** Every `/score` call does two synchronous `psycopg2.commit()` — one in score_logger.py (score log) and one in feature_logger.py (feature log). A single Postgres commit takes 2–10ms. Two per request = 4–20ms added to latency, and at 1000 TPS there's a single shared connection acting as a global lock.

**Fix:** Move both loggers off the critical path using `asyncio.Queue` + a background writer task.

Architecture:
```
score_endpoint (async)
    └─► put to asyncio.Queue  ← non-blocking, ~0ms
              ↓ (background task)
         asyncpg connection pool → Postgres (batched inserts)
```

**Implementation steps:**
1. Replace `psycopg2` in both loggers with `asyncpg` + a connection pool (`asyncpg.create_pool(min_size=5, max_size=20)`)
2. In main.py startup, create an `asyncio.Queue` and launch a background `asyncio.Task` that drains the queue and batch-inserts to Postgres every 50ms or every 100 rows, whichever comes first
3. `score_transaction()` calls `log_queue.put_nowait(payload)` — zero blocking

This alone can recover ~15–25ms per request and removes the shared-connection bottleneck.

---

### Phase 2 — Make the endpoint fully async + parallelize feature fetches

**The problem:** `score_endpoint` is `def`, not `async def`. FastAPI runs sync endpoints in a threadpool (`run_in_executor`), which caps total concurrency at the threadpool size (default 40 threads). At 1000 TPS this saturates within milliseconds.

**Fix — main.py:** Change to `async def score_endpoint` and `async def score_transaction`.

**Fix — feature fetching in scoring.py:** Feast and Redis are called sequentially today:
```python
# Today — sequential, ~20ms Feast + ~8ms Redis = 28ms
offline_feats, feast_ok = fetch_offline_features(...)
online_feats, redis_ok  = fetch_online_features(...)
```

Replace with concurrent execution:
```python
# After — parallel, max(Feast, Redis) = ~20ms
offline_feats_fut, online_feats_fut = await asyncio.gather(
    fetch_offline_features_async(...),
    fetch_online_features_async(...),
)
```

This saves the full Redis fetch time (~5–10ms) from the critical path.

---

### Phase 3 — Pipeline all Redis reads into one round trip

**The problem:** `get_all_online_features()` in retriever.py makes 6–8 separate `ZRANGEBYSCORE` calls sequentially. Each is a Redis round trip (~0.5–1ms each locally, 2–5ms over network). At 1000 TPS that's 6000–8000 Redis ops/sec, all serialized within each request.

**Fix:** Use a Redis pipeline to batch all reads into a single round trip:

```python
async def get_all_online_features_pipelined(user_id, device_id, now):
    async with redis_pool.pipeline(transaction=False) as pipe:
        cutoffs = {w: now - w for w in [WINDOW_5M, WINDOW_10M, WINDOW_1H]}
        pipe.zrangebyscore(user_txn_zset(user_id), cutoffs[WINDOW_5M], "+inf")
        pipe.zrangebyscore(user_txn_zset(user_id), cutoffs[WINDOW_10M], "+inf")
        pipe.zrangebyscore(user_txn_zset(user_id), cutoffs[WINDOW_1H], "+inf")
        pipe.zrangebyscore(user_merchant_zset(user_id), cutoffs[WINDOW_5M], "+inf")
        pipe.zrangebyscore(user_merchant_zset(user_id), cutoffs[WINDOW_10M], "+inf")
        pipe.zrangebyscore(user_merchant_zset(user_id), cutoffs[WINDOW_1H], "+inf")
        pipe.zrangebyscore(device_txn_zset(device_id), cutoffs[WINDOW_5M], "+inf")
        pipe.zrangebyscore(device_txn_zset(device_id), cutoffs[WINDOW_10M], "+inf")
        pipe.zrangebyscore(device_txn_zset(device_id), cutoffs[WINDOW_1H], "+inf")
        pipe.zrangebyscore(user_login_fail_zset(user_id), 15 * 60, "+inf")
        results = await pipe.execute()
    # unpack results...
```

This collapses 8 round trips into 1. Redis pipeline latency is essentially the same as a single command.

**Also:** Replace the `redis-py` sync client with `redis.asyncio.Redis(connection_pool=...)` with an explicit pool sized for your concurrency target:
```python
redis.asyncio.ConnectionPool(max_connections=200)
```

---

### Phase 4 — Bypass Feast SDK for offline features (optional, high gain)

**The problem:** Feast's `get_online_features()` SDK call adds its own overhead — internal serialization, validation, and Python object construction — on top of the actual Redis/SQLite lookup. For 30+ features at 1000 TPS, this accumulates.

**Option A (low effort):** Keep Feast but add an in-process LRU cache on offline features. User/device/merchant batch features are materialized once and change only when you re-run the pipeline. Cache with `cachetools.TTLCache` keyed by `(user_id, device_id, merchant_id)` with TTL of 60 seconds:
```python
from cachetools import TTLCache
_offline_cache = TTLCache(maxsize=50_000, ttl=60)
```
For a demo with a fixed synthetic user pool, cache hit rate will be near 100%, making offline feature fetch cost ~0ms.

**Option B (more effort, production-grade):** Bypass Feast SDK entirely. Since Feast materializes to Redis, query Redis directly using Feast's documented key schema:
```
feast:{project}:{feast_view_name}:{base64-encoded-entity-key}
```
This removes the SDK layer and makes offline feature reads identical in speed to your online feature reads.

---

### Phase 5 — Scale uvicorn workers + tune for concurrency

**Current `make start-api`:** Single uvicorn worker process.

For the demo, run with multiple workers behind gunicorn:
```bash
gunicorn app.main:app \
  -w 4 \                        # 4 workers = 4 CPU cores
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --worker-connections 1000 \
  --backlog 2048 \
  --timeout 30
```

With 4 async workers and the above fixes in place, each worker can handle hundreds of concurrent requests — well above 1000 TPS on a laptop for this workload.

For Docker: update docker-compose.yml to set `replicas: 3` on the `api` service + add an nginx load balancer service.

---

### Phase 6 — Load testing to validate and find residual bottlenecks

Add `locust` to requirements.txt and a `locustfile.py`:

```python
from locust import HttpUser, task, between

class FraudScoringUser(HttpUser):
    wait_time = between(0, 0.001)  # ~1000 TPS per worker

    @task
    def score(self):
        self.client.post("/score", json={
            "transaction_id": f"txn-{uuid.uuid4()}",
            "user_id": f"u_{random.randint(1, 500):06d}",
            "device_id": f"d_{random.randint(1, 1000):07d}",
            "merchant_id": f"m_{random.randint(1, 50):05d}",
            "amount": round(random.uniform(10, 500), 2),
            "is_international": False,
            "local_hour": 14,
        })
```

Run: `locust -f locustfile.py --headless -u 500 -r 50 --host http://localhost:8000`

This gives you real p50/p95/p99 latency numbers and TPS ceiling to show in the demo.

---

### Phase 7 — gRPC (only if Phase 6 shows serialization overhead)

After all the above, if profiling at 1000+ TPS still shows HTTP/JSON serialization in the hot path (it usually won't with a 10-field payload), then add a gRPC endpoint **alongside** the REST endpoint rather than replacing it — clients that need max throughput can use gRPC, others keep REST.

---

### Implementation order for the demo

```
Phase 1 (async DB logging)        ← 1–2 days, removes worst bottleneck
Phase 2 (async endpoint + gather) ← 1 day
Phase 3 (Redis pipeline)          ← half a day
Phase 4A (offline feature cache)  ← 2 hours
Phase 5 (gunicorn multi-worker)   ← 1 hour
Phase 6 (locust test + tune)      ← 1 day
Phase 7 (gRPC, if needed)         ← 2–3 days
```

### Expected latency breakdown after Phases 1–5

| Step | Before | After |
|---|---|---|
| Async overhead (threadpool) | ~5ms | ~0ms |
| Feast offline fetch | ~15ms | ~0ms (cache hit) |
| Redis online fetch (8 round trips) | ~8ms | ~2ms (pipeline) |
| XGBoost inference | ~1ms | ~1ms |
| score_logger commit | ~8ms | ~0ms (queue) |
| feature_logger commit | ~8ms | ~0ms (queue) |
| **Total** | **~45ms** | **~5–8ms** |

At ~8ms avg latency per request with 4 async workers, 1000–1500 TPS is well within reach on commodity hardware — and your demo will have real load test numbers to back it up.