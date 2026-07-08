-- ============================================================================
-- ClickHouse raw schema — target tables for Postgres → ClickHouse export.
--
-- Applied idempotently by scripts/export_pg_to_clickhouse.py before it runs
-- TRUNCATE + INSERT SELECT FROM postgresql(...).
--
-- Design notes
--   • High-volume tables (transactions, login events) partition monthly on
--     event_timestamp and sort by (user_id, event_timestamp) for efficient
--     point-in-time joins and per-user history reads.
--   • Reference tables (users, merchants, labels) skip partitioning.
--   • Postgres INET → ClickHouse String (INET not supported by the
--     postgresql() table function; cast in the SELECT clause).
--   • Postgres NUMERIC → ClickHouse Decimal. dbt staging models cast to
--     Float64 to avoid Decimal serialization issues in Feast/parquet.
-- ============================================================================

-- ── raw_users (reference, small) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.raw_users (
    user_id             String,
    email               String,
    phone               Nullable(String),
    country_code        LowCardinality(String),
    signup_date         Date,
    account_type        LowCardinality(String),
    is_verified         UInt8,
    event_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
ORDER BY (user_id);

-- ── raw_devices (event log, partitioned) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.raw_devices (
    device_event_id     UInt64,
    device_id           String,
    user_id             String,
    device_fingerprint  Nullable(String),
    platform            LowCardinality(Nullable(String)),
    os_version          Nullable(String),
    ip_address          Nullable(String),        -- Postgres INET, cast to text on export
    country_code        LowCardinality(Nullable(String)),
    event_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp, device_id);

-- ── raw_merchants (reference, small) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.raw_merchants (
    merchant_id         String,
    merchant_name       String,
    merchant_category   LowCardinality(String),
    country_code        LowCardinality(String),
    is_online           UInt8,
    risk_tier           LowCardinality(String),
    event_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
ORDER BY (merchant_id);

-- ── raw_transactions (highest volume, partitioned) ───────────────────────
CREATE TABLE IF NOT EXISTS raw.raw_transactions (
    transaction_id      String,
    user_id             String,
    device_id           String,
    merchant_id         String,
    amount              Decimal(18, 4),
    currency            LowCardinality(String),
    payment_method      LowCardinality(String),
    country_code        LowCardinality(String),
    ip_address          Nullable(String),        -- Postgres INET
    is_international    UInt8,
    txn_status          LowCardinality(String),
    decline_reason      LowCardinality(Nullable(String)),
    local_hour          Nullable(Int16),
    event_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp, transaction_id);

-- ── raw_login_events (moderate volume, partitioned) ──────────────────────
CREATE TABLE IF NOT EXISTS raw.raw_login_events (
    login_event_id      UInt64,
    user_id             String,
    device_id           Nullable(String),
    ip_address          Nullable(String),        -- Postgres INET
    country_code        LowCardinality(Nullable(String)),
    login_status        LowCardinality(String),
    failure_reason      LowCardinality(Nullable(String)),
    event_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMM(event_timestamp)
ORDER BY (user_id, event_timestamp);

-- ── fraud_labels (reference, keyed on transaction_id) ────────────────────
CREATE TABLE IF NOT EXISTS raw.fraud_labels (
    transaction_id      String,
    is_fraud            UInt8,
    fraud_type          LowCardinality(Nullable(String)),
    label_source        LowCardinality(String),
    label_timestamp     DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    created_at          DateTime64(6, 'UTC')
) ENGINE = MergeTree
ORDER BY (transaction_id);
