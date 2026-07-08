# Phase 2 — Production Integration & Real-Time Analytics

> **Duration**: 3-4 weeks
> **Merged from**: Phase 2 (Streaming Pipeline) + Phase 3 (Durable Store) — significantly simplified after removing Debezium as the primary path, ksqlDB, Kafka Connect sinks, and ScyllaDB (see §2.7 Design Decisions).
> **Goal**: Move from simulator-driven streaming to production source integration; add server-side real-time analytics via ClickHouse; harden Redis as the sole online hot store.
> **Prerequisite**: Phase 1 complete — Redpanda broker + Schema Registry, ClickHouse (with 4 RBAC roles), Redis, and 3 Python consumer groups running against the simulator.

---

## 2.1 What This Phase Delivers

By the end of Phase 2, FraudML has:
- **Production source integration via the transactional outbox pattern**: Predator writes an event to its DB **and** to an `outbox_events` table in the same DB transaction; a lightweight `outbox-relay` service publishes from the outbox to Redpanda. Guarantees at-least-once delivery matching DB commits. **Debezium CDC is kept as a fallback** only for legacy sources that cannot be modified.
- **ClickHouse Kafka Engine** ingesting all `txn.raw.<channel>` topics server-side — no dedicated Python `analytics-sink` consumer needed.
- **ClickHouse Materialized Views** maintaining 5m/10m/1h/24h rolling aggregates continuously (`main.mv_user_velocity`, `main.mv_device_velocity`) — replaces ksqlDB entirely.
- **`main.mv_latest_features`** — ClickHouse MV holding the current feature vector per entity, serving as **cold-read fallback** when Redis is unavailable.
- **Redis Cluster (3 primary + 3 replica)** with AOF persistence — the sole online hot store. ScyllaDB removed.
- **Simulator retired** from production configs (kept for load testing and demos).

---

## 2.2 Architecture After Phase 2

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           PHASE 2 ARCHITECTURE                               │
│                                                                              │
│  Predator microservices (production)                                         │
│      │                                                                       │
│      ▼  BEGIN; INSERT transactions; INSERT outbox_events; COMMIT;            │
│  ┌── Predator DB ─────────────────────────────┐                              │
│  │  transactions │ login_events │ outbox_events                              │
│  └──────┬───────────────────────────┬─────────┘                              │
│         │ (system of record)        │                                        │
│         │                           │  SELECT ... FOR UPDATE SKIP LOCKED     │
│         │                           ▼                                        │
│         │                    ┌─ outbox-relay ─┐                              │
│         │                    │  (aiokafka +   │  idempotent producer         │
│         │                    │   AvroSerdes)  │  batch = 500, flush = 100ms  │
│         │                    └────────┬───────┘                              │
│         │                             │                                      │
│         │                             ▼                                      │
│         │                    ┌── Redpanda 3-node HA ────┐                    │
│         │  Legacy fallback   │  txn.raw.<channel> × 6   │  Schema Registry   │
│         │  ── Debezium ────► │  txn.scored              │  (Avro subjects)   │
│         │                    │  login.events            │                    │
│         │                    └──┬──────────────┬────────┘                    │
│         │                       │              │                             │
│         │             ┌─────────┴──┐   ┌───────┴─────────┐   ┌─────────────┐ │
│         │             │  fraud-    │   │ feature-store-  │   │ ClickHouse  │ │
│         │             │  decision  │   │ updater         │   │ Kafka       │ │
│         │             │  (Python)  │   │ (Python)        │   │ Engine      │ │
│         │             │  ↓ POST    │   │ ↓ Redis sorted  │   │ (SQL, no    │ │
│         │             │   /score   │   │   sets + Feast  │   │  Python)    │ │
│         │             │  ↓ publish │   │   KV cache      │   │             │ │
│         │             │   txn.     │   │                 │   │ ↓ MVs       │ │
│         │             │   scored   │   │                 │   │             │ │
│         │             └────────────┘   └────────┬────────┘   └──────┬──────┘ │
│         │                                       │                   │        │
│         │                                       ▼                   ▼        │
│         │                          ┌─ Redis Cluster ────┐   ┌─ ClickHouse ─┐ │
│         │                          │  3 primary + 3     │   │ raw.txn_     │ │
│         │                          │  replica, AOF      │   │  kafka       │ │
│         │                          │  Sole HOT store    │   │ → main.txns  │ │
│         │                          │  velocity + Feast  │   │ → mv_        │ │
│         │                          │  KV                │   │  velocity    │ │
│         │                          │  Failover: 15-30s  │   │ → mv_latest_ │ │
│         │                          │  → replica reads   │   │  features    │ │
│         │                          │  → CH cold fallback│   │ (cold read   │ │
│         │                          └────────────────────┘   │  fallback)   │ │
│         │                                                    └──────────────┘ │
│         │                                                                    │
│  Postgres remains: model_score_log, model_meta, outbox_events (in Predator)  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2.3 Work Breakdown

### 2.3.1 Source Integration — Transactional Outbox (primary) or CDC (fallback)

| Task | Description | Days |
|------|-------------|------|
| **Outbox schema in Predator DB** | Add `outbox_events` table (id, aggregate_id, topic, key, avro_value bytea, created_at, published_at). Index on `(published_at IS NULL, id)`. | 0.5 |
| **Application-level write pattern** | Predator services: within each transaction, INSERT into both `transactions` and `outbox_events`. Payload serialized to Avro using shared schema. | 1 |
| **`outbox-relay` service** | New lightweight Python service in `streaming/outbox_relay.py`. Polls unpublished rows with `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 500`, publishes to Redpanda with idempotent producer, marks `published_at`. Runs 2 replicas for HA (SKIP LOCKED prevents duplicates). | 2 |
| **Redpanda 3-node HA upgrade** | Scale single-node Phase 1 broker to 3-node cluster. KRaft mode (no ZooKeeper). Update `--seeds` config. Rolling restart. | 1 |
| **Debezium fallback path** | Keep Debezium connector configs (SQL Server / PG / MySQL) as an alternative when the source system cannot be modified. Same target topics via `RegexRouter` SMT to `txn.raw.<channel>`. Consumers unchanged. | 2 |
| **Integration test — outbox** | Write to Predator DB within a transaction → verify event lands in Redpanda within 2s; assert no duplicates after simulating relay crash mid-batch. | 1 |
| **Integration test — CDC fallback** | Enable Debezium on a test DB; assert per-channel topics receive routed events. | 1 |

**Subtotal: ~8.5 days**

#### Outbox schema

```sql
-- Applied in Predator DB (Phase 2 migration)
CREATE TABLE outbox_events (
    id            BIGSERIAL PRIMARY KEY,
    aggregate_id  TEXT NOT NULL,           -- e.g. transaction_id
    topic         TEXT NOT NULL,           -- e.g. txn.raw.visa
    partition_key TEXT NOT NULL,           -- e.g. user_id (drives Redpanda partitioning)
    avro_value    BYTEA NOT NULL,          -- pre-serialized Avro payload
    schema_id     INT NOT NULL,            -- Schema Registry subject version
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    published_at  TIMESTAMPTZ NULL
);

CREATE INDEX idx_outbox_unpublished
    ON outbox_events (id)
    WHERE published_at IS NULL;
```

#### Relay loop (simplified)

```python
# streaming/outbox_relay.py — 2 replicas run concurrently, SKIP LOCKED avoids duplicates
async def relay_batch(pg, producer):
    async with pg.transaction():
        rows = await pg.fetch("""
            SELECT id, topic, partition_key, avro_value
            FROM outbox_events
            WHERE published_at IS NULL
            ORDER BY id
            LIMIT 500
            FOR UPDATE SKIP LOCKED
        """)
        if not rows:
            await asyncio.sleep(0.1)
            return

        futures = [
            producer.send_and_wait(r["topic"], r["avro_value"], key=r["partition_key"].encode())
            for r in rows
        ]
        await asyncio.gather(*futures)

        await pg.executemany(
            "UPDATE outbox_events SET published_at = NOW() WHERE id = $1",
            [(r["id"],) for r in rows],
        )
```

#### Debezium fallback (retained for legacy source integration)

```json
{
  "name": "source-db-connector",
  "config": {
    "connector.class": "io.debezium.connector.sqlserver.SqlServerConnector",
    "topic.prefix": "cdc.source",
    "transforms": "unwrap,route",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
    "transforms.route.regex": "cdc\\.source\\.transactions",
    "transforms.route.replacement": "txn.raw.${channel}",
    "key.converter.schema.registry.url": "http://redpanda:8081",
    "value.converter.schema.registry.url": "http://redpanda:8081"
  }
}
```

| Source DB | Debezium Connector | Setup Complexity | When to prefer over outbox |
|-----------|-------------------|-----------------|----------------------------|
| SQL Server | `SqlServerConnector` | Medium (`sys.sp_cdc_enable_table`) | Legacy core banking that cannot be modified |
| PostgreSQL | `PostgresConnector` | Low (`wal_level = logical`) | Same |
| MySQL | `MySqlConnector` | Low (`binlog_format = ROW`) | Same |
| Oracle | `OracleConnector` | High (LogMiner licensing) | When the core is Oracle-based |

**Rule of thumb**: use outbox when you own the source app; use CDC when you don't.

### 2.3.2 ClickHouse Kafka Engine + Materialized Views (Replaces ksqlDB + Sink Connectors)

| Task | Description | Days |
|------|-------------|------|
| **Kafka Engine tables** | `raw.txn_kafka` reading all 6 `txn.raw.*` topics via `KAFKA_TOPIC_LIST`. `raw.scored_kafka` reading `txn.scored`. `raw.login_kafka` reading `login.events`. All use `AvroConfluent` + Schema Registry. | 1 |
| **Landing MVs** | `main.mv_transactions_ingest` drains `raw.txn_kafka` → `main.transactions` (MergeTree, partitioned by month, ordered by `(user_id, event_timestamp)`). Same for scored and login. | 1 |
| **Rolling velocity MVs** | `main.mv_user_velocity_5m / 10m / 1h / 24h` using `AggregatingMergeTree` with `countState`, `sumState`, `uniqExactState`. Query with `-Merge` combinator at read time. | 2 |
| **Latest-feature MV** | `main.mv_latest_features` using `ReplacingMergeTree(event_timestamp)` keyed by `(entity_type, entity_id)` — cold-read fallback when Redis is down. | 1 |
| **Scoring service cold-fallback path** | `serving/feature_service.py`: on Redis timeout / error, fall back to `SELECT * FROM main.mv_latest_features FINAL WHERE ...`. Logged as degraded mode. Circuit breaker resets when Redis recovers. | 2 |
| **Integration test** | Publish 1000 events to `txn.raw.visa` → assert row count in `main.transactions` and correct aggregates in `mv_user_velocity_5m`. | 1 |

**Subtotal: ~8 days**

#### Kafka Engine + landing MV

```sql
-- ClickHouse consumes directly from Redpanda — no Python analytics-sink needed
CREATE TABLE raw.txn_kafka (
    transaction_id  String,
    user_id         String,
    device_id       String,
    merchant_id     String,
    channel         LowCardinality(String),
    amount          Float64,
    currency        LowCardinality(String),
    is_international UInt8,
    event_timestamp DateTime64(3, 'UTC')
) ENGINE = Kafka SETTINGS
    kafka_broker_list      = 'redpanda:9092',
    kafka_topic_list       = 'txn.raw.visa,txn.raw.mastercard,txn.raw.amex,txn.raw.qris,txn.raw.debit,txn.raw.digital',
    kafka_group_name       = 'clickhouse-analytics',
    kafka_format           = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers    = 3;

CREATE TABLE main.transactions (
    transaction_id  String,
    user_id         String,
    device_id       String,
    merchant_id     String,
    channel         LowCardinality(String),
    amount          Float64,
    currency        LowCardinality(String),
    is_international UInt8,
    event_timestamp DateTime64(3, 'UTC'),
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp);

CREATE MATERIALIZED VIEW main.mv_transactions_ingest
TO main.transactions
AS SELECT * FROM raw.txn_kafka;
```

#### Velocity aggregation MVs (replaces all ksqlDB queries)

```sql
CREATE TABLE main.user_velocity_5m (
    user_id                  String,
    window_start             DateTime,
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_merchants_state AggregateFunction(uniqExact, String)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (user_id, window_start);

CREATE MATERIALIZED VIEW main.mv_user_velocity_5m
TO main.user_velocity_5m
AS SELECT
    user_id,
    toStartOfInterval(event_timestamp, INTERVAL 5 MINUTE) AS window_start,
    countState() AS txn_count_state,
    sumState(amount) AS txn_amount_state,
    uniqExactState(merchant_id) AS distinct_merchants_state
FROM raw.txn_kafka
GROUP BY user_id, window_start;

-- Same pattern for 10m, 1h, 24h; and for device_velocity_5m/10m/1h
-- Consumers or analysts query with:
--   SELECT user_id,
--          countMerge(txn_count_state)          AS txn_count_5m,
--          sumMerge(txn_amount_state)           AS txn_amount_5m,
--          uniqExactMerge(distinct_merchants_state) AS distinct_merchants_5m
--   FROM main.user_velocity_5m
--   WHERE window_start >= now() - INTERVAL 5 MINUTE
--   GROUP BY user_id;
```

#### Latest-feature MV — cold-read fallback

```sql
CREATE TABLE main.latest_features (
    entity_type LowCardinality(String),   -- 'user' | 'device' | 'merchant'
    entity_id   String,
    features    Map(String, Float64),     -- feature name → value
    event_timestamp DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(event_timestamp)
ORDER BY (entity_type, entity_id);

-- Populated by upstream feature-store-updater consumer OR by a periodic dbt run.
-- Scoring service reads with FINAL to get latest values per entity when Redis is unavailable.
```

#### Server-side vs application-side comparison

| Aspect | Redis sorted sets (Phase 1, kept) | ClickHouse MVs (Phase 2, new) | ksqlDB (removed) |
|--------|-----------------------------------|-------------------------------|------------------|
| **Latency** | ~1-2ms (feature fetch at inference) | Not on hot path | N/A |
| **Purpose** | Feature serving | Analytics, cold fallback, BI | Feature computation |
| **Ops** | Redis Cluster only | ClickHouse only | Java + state store |
| **State recovery** | AOF replay + Feast rematerialize | ReplacingMergeTree self-healing | Rocks-DB local state, backup complex |

### 2.3.3 Redis Cluster + Cold-Fallback Read Path (Replaces ScyllaDB)

| Task | Description | Days |
|------|-------------|------|
| **Redis Cluster deployment** | 3 primary + 3 replica, AOF `everysec`, RDB snapshots hourly. Cluster mode enabled with `--cluster-enabled yes`. | 1 |
| **Client-side Cluster support** | `serving/feature_service.py`: switch from `redis.asyncio.Redis` to `redis.asyncio.RedisCluster` with `read_from_replicas=True`. Verify all existing sorted-set + hash operations work in Cluster mode (hash tags for co-location where needed). | 1 |
| **Feast Cluster mode** | Update `feast_store.yaml` online store to `redis_cluster` with `connection_string: "redis-node-1:6379,redis-node-2:6379,..."`. | 0.5 |
| **Cold-fallback circuit breaker** | On Redis error / timeout: switch to ClickHouse `main.latest_features FINAL` for feature reads. Log degraded mode. Retry Redis every 5s; resume hot path on 3 consecutive successes. | 1.5 |
| **AOF + snapshot verification** | Kill Redis primary → replica promotes → verify no data loss ≥ 1s. RDB backup restore drill. | 1 |
| **Memory sizing benchmark** | Load features for 30M users into Redis Cluster; measure memory (target: < 20 GB total across cluster). | 0.5 |
| **Integration test — failover** | Kill primary node during load test → scoring continues via replica (<30s degraded read window) → primary recovered → cluster balances. | 1 |
| **Integration test — cold fallback** | Kill entire Redis Cluster → scoring falls back to ClickHouse (P50 ~50ms); restart Redis → circuit breaker closes, hot path resumes. | 0.5 |

**Subtotal: ~7 days**

#### Why Redis alone is sufficient at bank scale

Typical Indonesian tier-1 bank (30M active customers, 26 batch features per user):
```
Per-user hash: ~600 bytes (features + Feast protobuf overhead)
Total memory:  30M × 600 B ≈ 18 GB
Redis Cluster: 6 nodes × 8 GB each = 48 GB capacity → 60% headroom
```

**Durability**: AOF `everysec` gives at most 1s of data loss on crash. RDB snapshots hourly. Feast rematerialization from ClickHouse is idempotent — full recovery in minutes if catastrophic.

**When to revisit and add a Cassandra-family store**: >100M entities × dense feature vectors, multi-region active-active with tunable consistency, or non-key lookups. Not a Phase 2 concern.

#### Cold-fallback pattern (simplified)

```python
# serving/feature_service.py
class FeatureService:
    def __init__(self, redis_cluster, clickhouse):
        self.redis = redis_cluster
        self.clickhouse = clickhouse
        self.circuit = CircuitBreaker(fail_threshold=3, recovery_timeout=5.0)

    async def fetch_user_features(self, user_id: str) -> dict:
        if self.circuit.is_closed():
            try:
                cached = await asyncio.wait_for(
                    self.redis.hgetall(f"feast:user:{user_id}"), timeout=0.05
                )
                if cached:
                    self.circuit.record_success()
                    return self._deserialize(cached)
            except (RedisError, asyncio.TimeoutError):
                self.circuit.record_failure()

        # Cold path — ClickHouse ReplacingMergeTree FINAL read (~50ms)
        metrics.COLD_FALLBACK.inc()
        row = await self.clickhouse.query(
            "SELECT features FROM main.latest_features FINAL "
            "WHERE entity_type = 'user' AND entity_id = %(uid)s",
            {"uid": user_id},
        )
        return dict(row["features"]) if row else self._default_user_features()
```

---

## 2.4 Integration with Predator Architecture

This phase implements the production **Database Layer** integration with Predator:

```
Predator microservices (owned by same platform team)
       │
       ▼   BEGIN; write transaction; write outbox_events; COMMIT;
  Predator DB ──► outbox_events ──► outbox-relay ──► Redpanda ──► FraudML consumers
       │                                                               │
       └── System of record for transactions                           │
                                                                       ▼
                                                   ┌── ClickHouse Kafka Engine
                                                   ├── feature-store-updater → Redis Cluster
                                                   └── fraud-decisioning → POST /score
```

**Key integration points**:
- **Outbox contract**: Predator services agree to the outbox schema and Avro payload. Backward-compatible schema evolution enforced by Schema Registry.
- **Redpanda topics**: `txn.raw.<channel>` are the FraudML–Predator contract surface. Producers (outbox-relay or Debezium) both target the same topics; consumers don't care which upstream is active.
- **Shared broker**: Same Redpanda cluster serves both Predator Rules SDK events and FraudML feature computation.
- **Fallback contract**: For clients running legacy Predator versions, Debezium CDC is available and terminates at the same topics — same downstream code.
- **Observability**: FraudML logs flow into the same ELK stack; consumer lag metrics exported to the shared Prometheus.

---

## 2.5 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | Outbox table + relay service running | INSERT into `transactions` + `outbox_events` in one txn → event appears in Redpanda within 2s |
| 2 | Relay HA — no duplicates | Run 2 relay replicas concurrently; assert 0 duplicate `transaction_id`s in `main.transactions` |
| 3 | Debezium fallback path validated | Disable outbox on test DB, enable Debezium → per-channel topics still populated; consumers unaware of change |
| 4 | Redpanda 3-node HA cluster | `rpk cluster health` reports 3 healthy nodes; kill 1 node → producers and consumers continue |
| 5 | ClickHouse Kafka Engine ingesting all 6 topics | Publish N events → `SELECT count() FROM main.transactions` matches N within 2s |
| 6 | Rolling velocity MVs live | Query `main.user_velocity_5m` returns correct counts vs Redis sorted-set values (parity < 1%) |
| 7 | Latest-features MV populated | Every scored entity has a row in `main.latest_features` |
| 8 | Redis Cluster (3P+3R) with AOF | Kill primary → replica promotes; no data loss beyond 1s |
| 9 | Cold-fallback read path | Stop entire Redis Cluster → scoring continues via ClickHouse (P50 ~50ms, still 0% failures) |
| 10 | Analytics-sink Python consumer **removed** | `streaming/consumers/analytics_sink.py` deleted; ClickHouse Kafka Engine handles that role |
| 11 | ScyllaDB **not deployed** | No `scylladb` service in docker-compose; no `scylla` dependencies in `requirements.txt` |
| 12 | End-to-end latency with production topology | Locust: 2K TPS, P50 < 20ms (hot path); cold-fallback P50 < 100ms |

---

## 2.6 Demo Checkpoint (D2)

### What to Show

1. **Outbox live demo**: Open two windows. Left — `psql` executing `BEGIN; INSERT transactions; INSERT outbox_events; COMMIT;`. Right — `rpk topic consume txn.raw.visa` printing the resulting Avro message in real time. Highlight: same DB transaction = same event, no dual-write risk.
2. **Relay HA drill**: Two `outbox-relay` replicas running. Kill one mid-batch. Show `SELECT count() FROM outbox_events WHERE published_at IS NULL` drop to zero. Assert no duplicate events downstream.
3. **ClickHouse Kafka Engine**: Query `SELECT count(), max(event_timestamp) FROM main.transactions` — updates in real time as events flow. No Python consumer in the loop.
4. **Real-time velocity aggregates**: Query `SELECT countMerge(txn_count_state) FROM main.user_velocity_5m WHERE user_id = 'u_042' AND window_start >= now() - INTERVAL 5 MINUTE` and watch it update as scoring happens.
5. **Redis Cluster failover**: Kill a Redis primary during 2K TPS Locust. Grafana shows a 15-30s dip while replica promotes; latency then returns to baseline. Zero request failures thanks to replica reads.
6. **Cold-fallback demo**: `docker stop $(docker ps -q --filter name=redis)` — kill the **entire** cluster. Scoring latency jumps to ~50ms P50 but keeps serving. Restart Redis → circuit breaker closes → hot path resumes automatically.
7. **Grafana**: Redpanda consumer lag per group, ClickHouse ingest rate, Redis cluster health, cold-fallback counter.

### Benchmark Targets

| Metric | Target |
|--------|--------|
| Outbox publish latency (DB commit → Redpanda) | < 2 seconds |
| ClickHouse Kafka Engine ingest lag | < 2 seconds |
| Redis Cluster failover time | < 30 seconds |
| Cold-fallback scoring P50 | < 100 ms |
| Hot-path scoring P50 (unchanged from Phase 1) | < 20 ms |
| Zero-duplicate outbox delivery | 100% (assert on 1M-event test) |

---

## 2.7 Design Decisions (What We Removed vs the Original Phase 2)

| Removed | Original role | Why removed | Replaced by |
|---------|---------------|-------------|-------------|
| **Debezium (as primary path)** | Capture DB writes for FraudML | Predator is being modernized — the app can publish natively. Also removes dual-write risk, DBA politics, and Kafka Connect ops. | **Transactional outbox pattern** (Predator writes event + outbox row in same DB transaction; relay publishes to Redpanda). Debezium kept as fallback for legacy sources only. |
| **ksqlDB** | Server-side sliding-window aggregates | Redis sorted sets already do this at the hot path (sub-ms). Analytics-side aggregation is a strict upgrade in ClickHouse MVs — same SQL, less infrastructure. | **ClickHouse `AggregatingMergeTree` MVs** (`user_velocity_5m/10m/1h/24h`, `device_velocity_*`). |
| **Kafka Connect sinks (Redis + ScyllaDB)** | Server-side write of aggregates to online stores | Python `feature-store-updater` consumer already updates Redis directly. ClickHouse Kafka Engine ingests server-side without Connect. | Existing Phase 1 Python consumer + ClickHouse Kafka Engine. |
| **ScyllaDB** | Durable online store, Redis fallback | Redis with AOF + Cluster is durable and scales to bank-tier customer counts. Rehydration from ClickHouse is idempotent. One less DB to operate. | **Redis Cluster (3P+3R) with AOF** as sole hot store, **ClickHouse `main.latest_features` MV** as cold-read fallback. |
| **Python `analytics-sink` consumer** | Batch-insert events into ClickHouse | ClickHouse Kafka Engine consumes topics natively via SQL. No Python needed for this path. | Kafka Engine + landing MVs. |
| **Dual-store feature service class** | Redis-first with ScyllaDB fallback | Redis is now the sole store. Fallback is to ClickHouse cold read, gated by a circuit breaker. | Circuit-breaker-gated ClickHouse fallback. |

**Result**: Phase 2 duration drops from 6-8 weeks to 3-4 weeks. Operational surface reduces from 4 new systems (Kafka + Connect + ksqlDB + ScyllaDB) to 2 (Redpanda already exists from Phase 1; outbox-relay is a small Python service; ClickHouse Kafka Engine is a configuration in an existing service).

---

## 2.8 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Predator team pushback on outbox pattern | Blocks primary source integration | Debezium fallback path is validated and equivalent from FraudML's perspective. Framing: "same broker, same topics, same schemas — you pick how the events get produced." |
| Outbox table growth | Unbounded row growth in Predator DB | Retention job deletes `published_at IS NOT NULL AND published_at < now() - INTERVAL 7 days`. Partition by day if volume warrants. |
| Dual outbox-relay replicas produce duplicates | Downstream duplicates break aggregation counts | `SELECT ... FOR UPDATE SKIP LOCKED` in relay + idempotent producer (`enable.idempotence=true`) guarantees at-most-once publish per row. Verified in D2 demo. |
| Redis Cluster failover window (15-30s) | Degraded scoring latency during promotion | Replica-read mode reduces to milliseconds for reads. Cold-fallback to ClickHouse keeps 100% availability. |
| ClickHouse MV lag under burst load | Analytics staleness | `AggregatingMergeTree` handles bursts; tune `kafka_max_block_size` and `kafka_num_consumers` in the Kafka Engine table. |
| Broker HA rolling upgrade | Requires careful sequence | Redpanda supports rolling upgrades via `rpk cluster health` gates. Runbook in Phase 5. |
| Avro schema breaking change | Producer-consumer breakage | Schema Registry enforces `BACKWARD` compatibility mode. CI check on `streaming/schemas/*.avsc` diffs blocks incompatible PRs. |
| Cold-fallback path under-tested | Silently broken until Redis outage in prod | Chaos-engineering test in CI kills Redis for 30s once per release cycle. |
