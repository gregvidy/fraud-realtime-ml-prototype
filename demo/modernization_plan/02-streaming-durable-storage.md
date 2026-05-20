# Phase 2 — Streaming Pipeline & Durable Feature Storage

> **Duration**: 6-8 weeks
> **Merged from**: Phase 2 (Streaming Pipeline) + Phase 3 (ScyllaDB Durable Store)
> **Goal**: Production-grade data ingestion via CDC, real-time streaming features, durable feature persistence
> **Prerequisite**: Phase 1 complete (containerized, ClickHouse operational)

---

## 2.1 What This Phase Delivers

By the end of Phase 2, FraudML has:
- **CDC pipeline**: Source DB changes captured automatically via Debezium → Kafka
- **Streaming features**: ksqlDB computes real-time aggregates (txn_count_5m, amount_10m, etc.)
- **Durable online store**: ScyllaDB persists full feature vectors (survives Redis restart)
- **Dual-read pattern**: Redis (hot, velocity) + ScyllaDB (warm, durable) with automatic fallback
- **Production-ready integration**: Connects to Predator's source DB without polling or batch exports

---

## 2.2 Architecture After Phase 2

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           PHASE 2 ARCHITECTURE                               │
│                                                                              │
│  Bank Channels                                                               │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─ API Gateway ─────────────────────────────────────────────────────────┐   │
│  │  /score → Scoring Pool    /train → Training Service                   │   │
│  └───────────────────────────────┬───────────────────────────────────────┘   │
│                                  │                                           │
│  ┌─ Scoring Plane ───────────────┼──────────────────────────────────────┐   │
│  │                               │                                      │   │
│  │  Scoring Service (N replicas) │                                      │   │
│  │  ├─ Feature fetch:            │                                      │   │
│  │  │   1. Redis (velocity + batch cache)      ◄─── hit: ~2ms          │   │
│  │  │   2. ScyllaDB fallback (durable store)   ◄─── miss: ~5-10ms     │   │
│  │  ├─ Model inference: LightGBM ThreadPool    ◄─── ~1ms              │   │
│  │  └─ Score logging: async queue → PostgreSQL                          │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Streaming Plane (NEW) ──────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  Source DB ──► Debezium CDC ──► Kafka ──► ksqlDB ──► Sink            │   │
│  │       │              │              │          │          │           │   │
│  │  (Predator      (captures      (event     (streaming   (writes to   │   │
│  │   writes          INSERTs,      broker)    aggregates)   Redis +     │   │
│  │   transactions)   UPDATEs)                               ScyllaDB)  │   │
│  │                                                                      │   │
│  │  ┌─ ksqlDB Streams ─────────────────────────────────────────────┐   │   │
│  │  │                                                               │   │   │
│  │  │  txn_stream (raw CDC events)                                  │   │   │
│  │  │    ├─► user_txn_count_5m   (HOPPING WINDOW 5min, advance 1m) │   │   │
│  │  │    ├─► user_txn_count_10m  (HOPPING WINDOW 10min)            │   │   │
│  │  │    ├─► user_txn_count_1h   (HOPPING WINDOW 1h)               │   │   │
│  │  │    ├─► user_txn_amount_5m  (SUM, HOPPING WINDOW 5min)        │   │   │
│  │  │    ├─► user_distinct_merchants_1h  (COUNT_DISTINCT, 1h)      │   │   │
│  │  │    ├─► device_txn_count_5m (HOPPING WINDOW 5min)             │   │   │
│  │  │    └─► user_failed_logins_15m (from login_events stream)     │   │   │
│  │  │                                                               │   │   │
│  │  │  Sink connectors:                                             │   │   │
│  │  │    ├─► Redis Sink (sorted sets for velocity features)         │   │   │
│  │  │    └─► ScyllaDB Sink (durable feature vectors)                │   │   │
│  │  │                                                               │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Data Stores ────────────────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  ┌─ Redis 7 ─────────────┐  ┌─ ScyllaDB ────────────────────────┐  │   │
│  │  │  Role: HOT CACHE       │  │  Role: DURABLE ONLINE STORE       │  │   │
│  │  │  ├ Velocity features   │  │  ├ Full feature vectors per entity│  │   │
│  │  │  │ (sorted sets, 1h)   │  │  ├ Batch features (from dbt/Feast)│  │   │
│  │  │  ├ Batch feature cache │  │  ├ Streaming features (from ksqlDB│  │   │
│  │  │  │ (Feast hash maps)   │  │  ├ TTL: 90 days                   │  │   │
│  │  │  ├ Model BLOBs         │  │  └ Partition key: entity_id       │  │   │
│  │  │  └ Pub/sub channels    │  │                                    │  │   │
│  │  │  TTL: 24h-10d          │  │  Latency: ~3-5ms (P99)            │  │   │
│  │  │  Latency: ~1-2ms       │  │  Throughput: 100K+ reads/s        │  │   │
│  │  └────────────────────────┘  └────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  ┌─ ClickHouse ──────────┐  ┌─ PostgreSQL ───────────────────────┐  │   │
│  │  │  Role: OFFLINE STORE   │  │  Role: METADATA + SCORE LOGS       │  │   │
│  │  │  dbt feature models    │  │  Training runs, model registry,    │  │   │
│  │  │  Training datasets     │  │  monitoring snapshots, score logs  │  │   │
│  │  └────────────────────────┘  └────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2.3 Work Breakdown

### 2.3.1 Kafka + Debezium CDC Pipeline

| Task | Description | Days |
|------|-------------|------|
| **Kafka cluster setup** | Kafka (or Redpanda) in Docker Compose. 3 brokers for production, 1 for dev. Schema Registry for Avro/JSON schema management. | 3 |
| **Debezium CDC connector** | Deploy Debezium Connect. Configure connector for source DB (SQL Server / PostgreSQL / MySQL). Capture: `transactions`, `login_events`, `users` table changes. | 3 |
| **CDC topic design** | Topics: `cdc.source.transactions`, `cdc.source.login_events`, `cdc.source.users`. Partitioned by entity_id for ordered processing. Retention: 7 days. | 1 |
| **Schema registry** | Register Avro schemas for each CDC topic. Enable schema evolution (backward compatible). | 1 |
| **Dead letter queue** | DLQ topic for failed messages. Alert on DLQ growth. | 0.5 |
| **Integration test** | Insert rows into source DB → verify they appear in Kafka topics within 2 seconds. | 1 |

**Subtotal: ~9.5 days**

#### Debezium Connector Config

```json
{
  "name": "source-db-connector",
  "config": {
    "connector.class": "io.debezium.connector.sqlserver.SqlServerConnector",
    "database.hostname": "${SOURCE_DB_HOST}",
    "database.port": "1433",
    "database.user": "${SOURCE_DB_USER}",
    "database.password": "${SOURCE_DB_PASSWORD}",
    "database.names": "Predator",
    "table.include.list": "dbo.Transactions,dbo.LoginEvents,dbo.Users",
    "topic.prefix": "cdc.source",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-history.source",
    "transforms": "unwrap",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.add.fields": "op,source.ts_ms",
    "key.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "key.converter.schema.registry.url": "http://schema-registry:8081",
    "value.converter.schema.registry.url": "http://schema-registry:8081",
    "snapshot.mode": "initial",
    "poll.interval.ms": 100,
    "max.batch.size": 2048
  }
}
```

#### CDC for Different Source DBs (Client Flexibility)

| Source DB | Debezium Connector | CDC Mechanism | Setup Complexity |
|-----------|-------------------|---------------|-----------------|
| SQL Server | `SqlServerConnector` | SQL Server CDC (built-in, requires `sys.sp_cdc_enable_table`) | Medium — DBA must enable CDC |
| PostgreSQL | `PostgresConnector` | Logical replication (`pgoutput` or `wal2json`) | Low — `ALTER SYSTEM SET wal_level = logical` |
| MySQL | `MySqlConnector` | Binary log (binlog) | Low — `binlog_format = ROW` |
| Oracle | `OracleConnector` | LogMiner or XStream | High — licensing implications |

### 2.3.2 ksqlDB Streaming Feature Computation

| Task | Description | Days |
|------|-------------|------|
| **ksqlDB deployment** | Add ksqlDB server to Docker Compose. Connect to Kafka cluster. | 1 |
| **Transaction stream** | Create `txn_stream` from `cdc.source.transactions` topic. Define schema. | 1 |
| **Velocity aggregates** | Hopping window queries for 14 real-time features (see below). Output to new topics. | 4 |
| **Login event stream** | Create `login_stream` for failed login velocity. | 1 |
| **Redis sink connector** | Kafka Connect sink → Redis. Write velocity features as sorted sets or hash maps. | 2 |
| **ScyllaDB sink connector** | Kafka Connect sink → ScyllaDB. Write aggregated features as entity rows. | 2 |
| **Backfill strategy** | For initial deployment: backfill ksqlDB from Kafka topic replay (snapshot mode). | 1 |
| **Integration test** | Insert transaction → verify Redis sorted set updated within 500ms. | 1 |

**Subtotal: ~13 days**

#### ksqlDB Streaming Queries

```sql
-- 1. Create stream from CDC topic
CREATE STREAM txn_stream (
    transaction_id VARCHAR KEY,
    user_id VARCHAR,
    device_id VARCHAR,
    merchant_id VARCHAR,
    amount DOUBLE,
    currency VARCHAR,
    is_international BOOLEAN,
    event_timestamp BIGINT  -- epoch millis from Debezium
)
WITH (
    KAFKA_TOPIC = 'cdc.source.transactions',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP = 'event_timestamp'
);

-- 2. User transaction count (5-minute hopping window, 1-minute advance)
CREATE TABLE user_txn_count_5m AS
SELECT
    user_id,
    COUNT(*) AS txn_count_5m,
    SUM(amount) AS txn_amount_5m,
    COUNT_DISTINCT(merchant_id) AS distinct_merchants_5m
FROM txn_stream
WINDOW HOPPING (SIZE 5 MINUTES, ADVANCE BY 1 MINUTE)
GROUP BY user_id
EMIT CHANGES;

-- 3. User transaction count (10-minute window)
CREATE TABLE user_txn_count_10m AS
SELECT
    user_id,
    COUNT(*) AS txn_count_10m,
    SUM(amount) AS txn_amount_10m,
    COUNT_DISTINCT(merchant_id) AS distinct_merchants_10m
FROM txn_stream
WINDOW HOPPING (SIZE 10 MINUTES, ADVANCE BY 1 MINUTE)
GROUP BY user_id
EMIT CHANGES;

-- 4. User transaction count (1-hour window)
CREATE TABLE user_txn_count_1h AS
SELECT
    user_id,
    COUNT(*) AS txn_count_1h,
    SUM(amount) AS txn_amount_1h,
    COUNT_DISTINCT(merchant_id) AS distinct_merchants_1h
FROM txn_stream
WINDOW HOPPING (SIZE 1 HOUR, ADVANCE BY 5 MINUTES)
GROUP BY user_id
EMIT CHANGES;

-- 5. Device transaction count (5m/10m/1h)
CREATE TABLE device_txn_count_5m AS
SELECT
    device_id,
    COUNT(*) AS txn_count_5m
FROM txn_stream
WINDOW HOPPING (SIZE 5 MINUTES, ADVANCE BY 1 MINUTE)
GROUP BY device_id
EMIT CHANGES;

-- 6. Login events stream
CREATE STREAM login_stream (
    event_id VARCHAR KEY,
    user_id VARCHAR,
    success BOOLEAN,
    event_timestamp BIGINT
)
WITH (
    KAFKA_TOPIC = 'cdc.source.login_events',
    VALUE_FORMAT = 'AVRO',
    TIMESTAMP = 'event_timestamp'
);

-- 7. Failed login count (15-minute + 1-hour windows)
CREATE TABLE user_failed_logins_15m AS
SELECT
    user_id,
    COUNT(*) AS failed_logins_15m
FROM login_stream
WHERE success = false
WINDOW HOPPING (SIZE 15 MINUTES, ADVANCE BY 1 MINUTE)
GROUP BY user_id
EMIT CHANGES;
```

#### ksqlDB vs Redis Sorted Sets (Current Prototype)

| Aspect | Current (Redis sorted sets) | ksqlDB (Phase 2) |
|--------|---------------------------|-------------------|
| **Computation** | Application-side (Python `ZRANGEBYSCORE`) | ksqlDB server-side (SQL) |
| **Trigger** | Scoring service writes events | CDC captures automatically |
| **Exactly-once** | Best-effort (app crash = lost event) | Kafka guarantees (exactly-once semantics) |
| **Backfill** | Manual replay | Kafka topic replay |
| **Scalability** | Single Redis node | ksqlDB scales with Kafka partitions |
| **Operational** | Simple (Redis only) | More infrastructure (Kafka + ksqlDB + Connect) |

**Why switch**: The prototype's Redis sorted sets work for the demo, but in production:
- Source DB writes happen in Predator (not in the ML service) — CDC is the only way to capture them
- Kafka provides exactly-once semantics and replay capability
- ksqlDB handles multiple tenants with partition-based isolation
- Decouples feature computation from the scoring hot path

### 2.3.3 ScyllaDB Durable Feature Store

| Task | Description | Days |
|------|-------------|------|
| **ScyllaDB deployment** | Add ScyllaDB to Docker Compose. Single node for dev, 3-node for prod. | 1 |
| **Schema design** | Entity-centric tables for users, devices, merchants. Wide rows with feature columns. | 2 |
| **Batch feature write path** | dbt → ClickHouse → Parquet → ScyllaDB (bulk load). Or: Feast materialization to ScyllaDB instead of Redis. | 3 |
| **Streaming feature write path** | ksqlDB → Kafka Connect Sink → ScyllaDB. Real-time features persisted. | 2 |
| **Read path: dual-store** | Scoring service: try Redis first → fallback to ScyllaDB. Populate Redis on ScyllaDB hit (write-back cache). | 3 |
| **TTL and compaction** | 90-day TTL on feature rows. ScyllaDB auto-compaction. | 0.5 |
| **Migration script** | Current Redis data → ScyllaDB (one-time backfill). | 1 |
| **Integration test** | Redis miss → ScyllaDB hit → correct features returned → Redis populated. | 1 |

**Subtotal: ~13.5 days**

#### ScyllaDB Schema

```cql
-- Keyspace (per-tenant or shared with tenant_id prefix)
CREATE KEYSPACE fraudml WITH replication = {
    'class': 'SimpleStrategy',    -- NetworkTopology for multi-DC
    'replication_factor': 3
};

USE fraudml;

-- User features (batch + streaming merged)
CREATE TABLE user_features (
    user_id TEXT,
    feature_version INT,           -- schema version for backward compat
    -- Batch features (from dbt/Feast)
    account_age_days INT,
    is_verified BOOLEAN,
    txn_count_1d INT,
    txn_count_7d INT,
    txn_count_30d INT,
    txn_amount_1d DOUBLE,
    txn_amount_7d DOUBLE,
    txn_amount_30d DOUBLE,
    distinct_merchants_7d INT,
    distinct_merchants_30d INT,
    distinct_devices_30d INT,
    decline_count_7d INT,
    failed_logins_7d INT,
    failed_logins_1d INT,
    -- Streaming features (from ksqlDB)
    txn_count_5m INT,
    txn_count_10m INT,
    txn_count_1h INT,
    txn_amount_5m DOUBLE,
    txn_amount_10m DOUBLE,
    txn_amount_1h DOUBLE,
    distinct_merchants_5m INT,
    distinct_merchants_10m INT,
    distinct_merchants_1h INT,
    failed_logins_15m INT,
    failed_logins_1h INT,
    -- Metadata
    batch_updated_at TIMESTAMP,
    streaming_updated_at TIMESTAMP,
    PRIMARY KEY (user_id)
) WITH default_time_to_live = 7776000   -- 90 days
  AND compaction = {'class': 'LeveledCompactionStrategy'};

-- Device features
CREATE TABLE device_features (
    device_id TEXT,
    feature_version INT,
    distinct_users_30d INT,
    txn_count_7d INT,
    txn_count_1d INT,
    is_shared_flag BOOLEAN,
    txn_count_5m INT,
    txn_count_10m INT,
    txn_count_1h INT,
    batch_updated_at TIMESTAMP,
    streaming_updated_at TIMESTAMP,
    PRIMARY KEY (device_id)
) WITH default_time_to_live = 7776000;

-- Merchant features
CREATE TABLE merchant_features (
    merchant_id TEXT,
    feature_version INT,
    is_high_risk BOOLEAN,
    is_online BOOLEAN,
    txn_count_30d INT,
    avg_ticket_30d DOUBLE,
    fraud_rate_30d DOUBLE,
    batch_updated_at TIMESTAMP,
    PRIMARY KEY (merchant_id)
) WITH default_time_to_live = 7776000;
```

#### Dual-Store Read Pattern (Scoring Service)

```python
# serving/feature_service.py

class DualStoreFeatureService:
    """Redis (hot) + ScyllaDB (durable) with write-back caching."""
    
    def __init__(self, redis_client, scylla_session):
        self.redis = redis_client
        self.scylla = scylla_session
        self.scylla_stmts = self._prepare_statements()
    
    async def fetch_user_features(self, user_id: str) -> dict:
        # 1. Try Redis first (sub-ms)
        cached = await self.redis.hgetall(f"feast:user:{user_id}")
        if cached:
            metrics.CACHE_HIT.labels(entity_type="user").inc()
            return self._deserialize_redis(cached)
        
        # 2. Fallback to ScyllaDB (~3-5ms)
        metrics.CACHE_MISS.labels(entity_type="user").inc()
        row = await self.scylla.execute_async(
            self.scylla_stmts["user"],
            [user_id]
        )
        if row:
            features = self._row_to_dict(row)
            # 3. Write-back to Redis (non-blocking)
            asyncio.create_task(
                self._populate_redis_cache(f"feast:user:{user_id}", features, ttl=60)
            )
            return features
        
        # 4. Entity not found — return defaults
        return self._default_user_features()
```

---

## 2.4 Integration with Predator Architecture

This phase directly implements the **Database Layer** from the Predator modernization diagram:

```
Predator Orchestrator                FraudML
       │                                │
       ▼                                │
  source db ──► Debezium ──► Kafka ──► ksqlDB ──► sink ──► ScyllaDB
       │              CDC pipeline (shared with Predator)       │
       │                                                         │
       └── Orchestrator writes transactions                     │
                                                                 │
  FraudML Scoring Service reads features ◄──────────────────────┘
```

**Key integration points**:
- **Debezium connector**: Same connector can capture events for both Predator Rules SDK and FraudML feature computation
- **Kafka topics**: Shared topic namespace — FraudML consumes from `cdc.source.*` topics
- **ScyllaDB**: Can be shared with Predator for non-ML feature storage, or isolated per service
- **ELK**: FraudML logs flow into the same ELK stack as Predator

---

## 2.5 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | Debezium CDC capturing source DB changes | Insert row in source DB → appears in Kafka topic within 2s |
| 2 | ksqlDB computing 14 streaming features | Query ksqlDB table → returns correct windowed aggregates |
| 3 | Redis sink receiving velocity features | `HGETALL fraud:user:{id}` returns streaming features |
| 4 | ScyllaDB storing full feature vectors | `SELECT * FROM user_features WHERE user_id = ?` returns batch + streaming |
| 5 | Dual-read scoring (Redis + ScyllaDB fallback) | Stop Redis → scoring still works via ScyllaDB (degraded latency) |
| 6 | Batch features in ScyllaDB | dbt → ClickHouse → ScyllaDB pipeline runs end-to-end |
| 7 | End-to-end latency with streaming | Locust: 2K TPS, P50 < 20ms (with streaming features active) |

---

## 2.6 Demo Checkpoint (D2)

### What to Show

1. **CDC live demo**: Insert a transaction into source DB → watch it flow through Kafka → ksqlDB → Redis/ScyllaDB
2. **ksqlDB query**: Show `SELECT * FROM user_txn_count_5m WHERE user_id = 'u_042'` updating in real-time
3. **Dual-store failover**: Kill Redis → scoring continues via ScyllaDB → restart Redis → cache repopulates
4. **Streaming features in score**: Score a transaction → response includes features computed from streaming pipeline
5. **Grafana**: Kafka consumer lag, ksqlDB throughput, ScyllaDB latency

### Benchmark Targets

| Metric | Target |
|--------|--------|
| CDC latency (source DB → Kafka) | < 2 seconds |
| ksqlDB processing (Kafka → aggregated topic) | < 1 second |
| Sink latency (Kafka → Redis + ScyllaDB) | < 500ms |
| End-to-end feature freshness | < 5 seconds |
| Scoring P50 (with streaming features) | < 20ms |
| ScyllaDB fallback latency (Redis down) | < 10ms |

---

## 2.7 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Debezium CDC overhead on source DB | Adds 1-5% load to source DB | Use CDC capture tables (SQL Server) or logical replication slots (PG) — minimal impact; test on replica first |
| Kafka infrastructure complexity | Ops burden increases significantly | Use Redpanda as alternative (single binary, Kafka-compatible, lower ops) |
| ksqlDB hopping windows consume memory | Large windows on high-cardinality entities | Limit window retention; use GRACE PERIOD to bound state |
| ScyllaDB write amplification | Frequent streaming updates cause compaction pressure | Use TimeWindowCompactionStrategy for streaming tables; LeveledCompaction for batch |
| Dual-store consistency | Redis and ScyllaDB may have different values briefly | Accept eventual consistency — Redis is always "more recent" for velocity features; ScyllaDB is source of truth for batch |
| DBA resistance to CDC on production DB | Blocks deployment | Offer read replica CDC as alternative (Debezium on replica, not primary) |
