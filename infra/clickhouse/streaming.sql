-- ============================================================================
-- ClickHouse streaming ingest — Slice 9
--
-- Adds server-side Kafka consumption via the ClickHouse Kafka Engine +
-- Materialized Views. Replaces the need for a Python `analytics-sink` process.
--
-- Layout
--   raw.stream_txn_kafka       — Kafka Engine, reads all 6 txn.raw.* topics
--   raw.stream_scored_kafka    — Kafka Engine, reads txn.scored
--   raw.stream_login_kafka     — Kafka Engine, reads login.events
--
--   main.stream_transactions   — MergeTree destination (row-level events)
--   main.stream_scored_txns    — MergeTree destination (scored + risk band)
--   main.stream_logins         — MergeTree destination (login events)
--
--   main.mv_stream_*_ingest    — MVs draining Kafka Engine → MergeTree
--
--   main.stream_user_velocity_5m / _1h / _24h   — AggregatingMergeTree windows
--   main.stream_device_velocity_5m / _1h
--   main.mv_stream_*_velocity_*                 — MVs feeding the above
--
--   main.stream_latest_features — ReplacingMergeTree cold-read fallback
--                                 (populated in slice 10 by feature-updater
--                                 or a periodic dbt run — table shape only
--                                 provisioned here)
--
-- Idempotence: script is re-runnable. Uses IF NOT EXISTS for MergeTree
-- destinations (to preserve data) and DROP+CREATE for Kafka Engine tables
-- and MVs (safe to recreate — offsets are stored in the __consumer_offsets
-- topic per group, not in the CH table).
--
-- Consumer groups (change the *_group_v1 suffix to replay from earliest):
--   clickhouse-analytics-v1  — reads txn.raw.*
--   clickhouse-scored-v1     — reads txn.scored
--   clickhouse-logins-v1     — reads login.events
--
-- Broker addr from CH's perspective: redpanda:29092  (INTERNAL listener)
-- Schema Registry:                    http://redpanda:8081
-- ============================================================================

-- ── raw.stream_txn_kafka  ← txn.raw.{visa,mastercard,amex,qris,debit,digital}
DROP TABLE IF EXISTS raw.stream_txn_kafka SYNC;

CREATE TABLE raw.stream_txn_kafka (
    transaction_id   String,
    user_id          String,
    device_id        String,
    merchant_id      String,
    channel          String,
    amount           Float64,
    currency         String,
    payment_method   String,
    country_code     String,
    is_international UInt8,
    txn_status       String,
    local_hour       Int32,
    event_timestamp  DateTime64(6, 'UTC'),
    ip_address       Nullable(String),
    is_fraud_sim     UInt8
) ENGINE = Kafka SETTINGS
    kafka_broker_list      = 'redpanda:29092',
    kafka_topic_list       = 'txn.raw.visa,txn.raw.mastercard,txn.raw.amex,txn.raw.qris,txn.raw.debit,txn.raw.digital',
    kafka_group_name       = 'clickhouse-analytics-v1',
    kafka_format           = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers    = 3,
    kafka_thread_per_consumer = 0,
    kafka_flush_interval_ms = 2000;


-- ── main.stream_transactions  (destination) ──────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_transactions (
    transaction_id      String,
    user_id             String,
    device_id           String,
    merchant_id         String,
    channel             LowCardinality(String),
    amount              Float64,
    currency            LowCardinality(String),
    payment_method      LowCardinality(String),
    country_code        LowCardinality(String),
    is_international    UInt8,
    txn_status          LowCardinality(String),
    local_hour          Int32,
    event_timestamp     DateTime64(6, 'UTC'),
    ip_address          Nullable(String),
    is_fraud_sim        UInt8,
    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp, transaction_id);


-- ── main.mv_stream_transactions_ingest  (Kafka → transactions) ───────────
DROP TABLE IF EXISTS main.mv_stream_transactions_ingest SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_transactions_ingest
TO main.stream_transactions
AS SELECT
    transaction_id,
    user_id,
    device_id,
    merchant_id,
    channel,
    amount,
    currency,
    payment_method,
    country_code,
    is_international,
    txn_status,
    local_hour,
    event_timestamp,
    ip_address,
    is_fraud_sim,
    now64(3) AS ingested_at
FROM raw.stream_txn_kafka;


-- ── raw.stream_scored_kafka  ← txn.scored ────────────────────────────────
DROP TABLE IF EXISTS raw.stream_scored_kafka SYNC;

CREATE TABLE raw.stream_scored_kafka (
    transaction_id           String,
    user_id                  String,
    device_id                String,
    merchant_id              String,
    channel                  String,
    amount                   Float64,
    is_international         UInt8,
    event_timestamp          DateTime64(6, 'UTC'),
    fraud_score              Float64,
    risk_band                String,
    is_flagged               UInt8,
    model_version            String,
    feature_service_version  String,
    scored_at                DateTime64(6, 'UTC')
) ENGINE = Kafka SETTINGS
    kafka_broker_list      = 'redpanda:29092',
    kafka_topic_list       = 'txn.scored',
    kafka_group_name       = 'clickhouse-scored-v1',
    kafka_format           = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers    = 3,
    kafka_thread_per_consumer = 0,
    kafka_flush_interval_ms = 2000;


CREATE TABLE IF NOT EXISTS main.stream_scored_txns (
    transaction_id           String,
    user_id                  String,
    device_id                String,
    merchant_id              String,
    channel                  LowCardinality(String),
    amount                   Float64,
    is_international         UInt8,
    event_timestamp          DateTime64(6, 'UTC'),
    fraud_score              Float64,
    risk_band                LowCardinality(String),
    is_flagged               UInt8,
    model_version            LowCardinality(String),
    feature_service_version  LowCardinality(String),
    scored_at                DateTime64(6, 'UTC'),
    ingested_at              DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp, transaction_id);


DROP TABLE IF EXISTS main.mv_stream_scored_ingest SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_scored_ingest
TO main.stream_scored_txns
AS SELECT
    transaction_id,
    user_id,
    device_id,
    merchant_id,
    channel,
    amount,
    is_international,
    event_timestamp,
    fraud_score,
    risk_band,
    is_flagged,
    model_version,
    feature_service_version,
    scored_at,
    now64(3) AS ingested_at
FROM raw.stream_scored_kafka;


-- ── raw.stream_login_kafka  ← login.events ───────────────────────────────
DROP TABLE IF EXISTS raw.stream_login_kafka SYNC;

CREATE TABLE raw.stream_login_kafka (
    login_event_id   Int64,
    user_id          String,
    device_id        Nullable(String),
    ip_address       Nullable(String),
    country_code     Nullable(String),
    login_status     String,
    failure_reason   Nullable(String),
    event_timestamp  DateTime64(6, 'UTC')
) ENGINE = Kafka SETTINGS
    kafka_broker_list      = 'redpanda:29092',
    kafka_topic_list       = 'login.events',
    kafka_group_name       = 'clickhouse-logins-v1',
    kafka_format           = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://redpanda:8081',
    kafka_num_consumers    = 2,
    kafka_thread_per_consumer = 0,
    kafka_flush_interval_ms = 2000;


CREATE TABLE IF NOT EXISTS main.stream_logins (
    login_event_id   Int64,
    user_id          String,
    device_id        Nullable(String),
    ip_address       Nullable(String),
    country_code     LowCardinality(Nullable(String)),
    login_status     LowCardinality(String),
    failure_reason   LowCardinality(Nullable(String)),
    event_timestamp  DateTime64(6, 'UTC'),
    ingested_at      DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp, login_event_id);


DROP TABLE IF EXISTS main.mv_stream_logins_ingest SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_logins_ingest
TO main.stream_logins
AS SELECT
    login_event_id,
    user_id,
    device_id,
    ip_address,
    country_code,
    login_status,
    failure_reason,
    event_timestamp,
    now64(3) AS ingested_at
FROM raw.stream_login_kafka;


-- ============================================================================
-- Rolling velocity aggregates (AggregatingMergeTree)
-- ============================================================================
-- Query pattern (analyst / consumer):
--     SELECT user_id,
--            countMerge(txn_count_state)              AS txn_count,
--            sumMerge(txn_amount_state)               AS txn_amount,
--            uniqExactMerge(distinct_merchants_state) AS distinct_merchants
--     FROM main.stream_user_velocity_5m
--     WHERE window_start >= now() - INTERVAL 5 MINUTE
--     GROUP BY user_id;

-- ── User velocity 5-minute buckets ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_user_velocity_5m (
    user_id                  String,
    window_start             DateTime('UTC'),
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_merchants_state AggregateFunction(uniqExact, String),
    intl_count_state         AggregateFunction(sum, UInt8)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (user_id, window_start);


DROP TABLE IF EXISTS main.mv_stream_user_velocity_5m SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_user_velocity_5m
TO main.stream_user_velocity_5m
AS SELECT
    user_id,
    toStartOfInterval(toDateTime(event_timestamp, 'UTC'), INTERVAL 5 MINUTE) AS window_start,
    countState()                    AS txn_count_state,
    sumState(amount)                AS txn_amount_state,
    uniqExactState(merchant_id)     AS distinct_merchants_state,
    sumState(is_international)      AS intl_count_state
FROM raw.stream_txn_kafka
GROUP BY user_id, window_start;


-- ── User velocity 1-hour buckets ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_user_velocity_1h (
    user_id                  String,
    window_start             DateTime('UTC'),
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_merchants_state AggregateFunction(uniqExact, String),
    intl_count_state         AggregateFunction(sum, UInt8)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (user_id, window_start);


DROP TABLE IF EXISTS main.mv_stream_user_velocity_1h SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_user_velocity_1h
TO main.stream_user_velocity_1h
AS SELECT
    user_id,
    toStartOfInterval(toDateTime(event_timestamp, 'UTC'), INTERVAL 1 HOUR) AS window_start,
    countState()                    AS txn_count_state,
    sumState(amount)                AS txn_amount_state,
    uniqExactState(merchant_id)     AS distinct_merchants_state,
    sumState(is_international)      AS intl_count_state
FROM raw.stream_txn_kafka
GROUP BY user_id, window_start;


-- ── User velocity 24-hour buckets ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_user_velocity_24h (
    user_id                  String,
    window_start             DateTime('UTC'),
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_merchants_state AggregateFunction(uniqExact, String),
    intl_count_state         AggregateFunction(sum, UInt8)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (user_id, window_start);


DROP TABLE IF EXISTS main.mv_stream_user_velocity_24h SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_user_velocity_24h
TO main.stream_user_velocity_24h
AS SELECT
    user_id,
    toStartOfInterval(toDateTime(event_timestamp, 'UTC'), INTERVAL 1 DAY) AS window_start,
    countState()                    AS txn_count_state,
    sumState(amount)                AS txn_amount_state,
    uniqExactState(merchant_id)     AS distinct_merchants_state,
    sumState(is_international)      AS intl_count_state
FROM raw.stream_txn_kafka
GROUP BY user_id, window_start;


-- ── Device velocity 5-minute buckets ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_device_velocity_5m (
    device_id                String,
    window_start             DateTime('UTC'),
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_users_state     AggregateFunction(uniqExact, String)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (device_id, window_start);


DROP TABLE IF EXISTS main.mv_stream_device_velocity_5m SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_device_velocity_5m
TO main.stream_device_velocity_5m
AS SELECT
    device_id,
    toStartOfInterval(toDateTime(event_timestamp, 'UTC'), INTERVAL 5 MINUTE) AS window_start,
    countState()                AS txn_count_state,
    sumState(amount)            AS txn_amount_state,
    uniqExactState(user_id)     AS distinct_users_state
FROM raw.stream_txn_kafka
GROUP BY device_id, window_start;


-- ── Device velocity 1-hour buckets ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS main.stream_device_velocity_1h (
    device_id                String,
    window_start             DateTime('UTC'),
    txn_count_state          AggregateFunction(count),
    txn_amount_state         AggregateFunction(sum, Float64),
    distinct_users_state     AggregateFunction(uniqExact, String)
) ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (device_id, window_start);


DROP TABLE IF EXISTS main.mv_stream_device_velocity_1h SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_device_velocity_1h
TO main.stream_device_velocity_1h
AS SELECT
    device_id,
    toStartOfInterval(toDateTime(event_timestamp, 'UTC'), INTERVAL 1 HOUR) AS window_start,
    countState()                AS txn_count_state,
    sumState(amount)            AS txn_amount_state,
    uniqExactState(user_id)     AS distinct_users_state
FROM raw.stream_txn_kafka
GROUP BY device_id, window_start;


-- ============================================================================
-- Latest features (ReplacingMergeTree) — cold-read fallback for slice 10.
-- Populated by an MV on raw.stream_txn_kafka: every incoming event emits a
-- row with the user's latest transaction context (amount, hour, intl flag,
-- country hash). ReplacingMergeTree(event_timestamp) dedups keeping the
-- newest row per (entity_type, entity_id). Query with FINAL to get the
-- deduplicated latest row.
--
-- The scoring service falls back to this table when Redis is unavailable,
-- filling in a REDUCED feature set so scoring can continue (higher latency,
-- lower accuracy than the hot path).
-- ============================================================================
CREATE TABLE IF NOT EXISTS main.stream_latest_features (
    entity_type      LowCardinality(String),   -- 'user' | 'device' | 'merchant'
    entity_id        String,
    features         Map(String, Float64),
    event_timestamp  DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(event_timestamp)
ORDER BY (entity_type, entity_id);


DROP TABLE IF EXISTS main.mv_stream_latest_features_user SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_latest_features_user
TO main.stream_latest_features
AS SELECT
    'user'                                        AS entity_type,
    user_id                                       AS entity_id,
    map(
        'last_txn_amount',      amount,
        'last_txn_local_hour',  toFloat64(local_hour),
        'last_is_international',toFloat64(is_international),
        'last_txn_intl_flag',   toFloat64(is_international)
    )                                             AS features,
    toDateTime64(event_timestamp, 3, 'UTC')       AS event_timestamp
FROM raw.stream_txn_kafka;


DROP TABLE IF EXISTS main.mv_stream_latest_features_device SYNC;

CREATE MATERIALIZED VIEW main.mv_stream_latest_features_device
TO main.stream_latest_features
AS SELECT
    'device'                                      AS entity_type,
    device_id                                     AS entity_id,
    map(
        'last_txn_amount',      amount,
        'last_txn_local_hour',  toFloat64(local_hour)
    )                                             AS features,
    toDateTime64(event_timestamp, 3, 'UTC')       AS event_timestamp
FROM raw.stream_txn_kafka;
