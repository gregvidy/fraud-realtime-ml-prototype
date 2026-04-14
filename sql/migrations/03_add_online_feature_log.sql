-- Migration: add online_feature_log table
-- Purpose : persist the exact online (Redis) feature values used at inference
--           time so training can join by transaction_id instead of re-deriving
--           them from dbt self-join SQL.
-- Run after: 02_add_feature_service_version.sql

CREATE TABLE IF NOT EXISTS online_feature_log (
    id                          BIGSERIAL       PRIMARY KEY,
    transaction_id              VARCHAR(64)     NOT NULL,
    user_id                     VARCHAR(64)     NOT NULL,
    device_id                   VARCHAR(64)     NOT NULL,
    scored_at                   TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- user sliding-window features (mirrors retriever.get_user_online_features)
    user_txn_count_5m           INTEGER         NOT NULL DEFAULT 0,
    user_txn_count_10m          INTEGER         NOT NULL DEFAULT 0,
    user_txn_count_1h           INTEGER         NOT NULL DEFAULT 0,
    user_txn_amount_sum_5m      NUMERIC(18, 4)  NOT NULL DEFAULT 0,
    user_txn_amount_sum_10m     NUMERIC(18, 4)  NOT NULL DEFAULT 0,
    user_txn_amount_sum_1h      NUMERIC(18, 4)  NOT NULL DEFAULT 0,
    user_distinct_merchants_5m  INTEGER         NOT NULL DEFAULT 0,
    user_distinct_merchants_10m INTEGER         NOT NULL DEFAULT 0,
    user_distinct_merchants_1h  INTEGER         NOT NULL DEFAULT 0,

    -- user login features (mirrors retriever.get_user_login_features)
    user_failed_logins_15m      INTEGER         NOT NULL DEFAULT 0,
    user_failed_logins_1h       INTEGER         NOT NULL DEFAULT 0,

    -- device sliding-window features (mirrors retriever.get_device_online_features)
    device_txn_count_5m         INTEGER         NOT NULL DEFAULT 0,
    device_txn_count_10m        INTEGER         NOT NULL DEFAULT 0,
    device_txn_count_1h         INTEGER         NOT NULL DEFAULT 0
);

-- Unique on transaction_id: one feature snapshot per inference call.
-- ON CONFLICT DO NOTHING in the logger prevents duplicate writes on retries.
CREATE UNIQUE INDEX IF NOT EXISTS idx_online_feature_log_txn_id
    ON online_feature_log(transaction_id);

CREATE INDEX IF NOT EXISTS idx_online_feature_log_user_id
    ON online_feature_log(user_id);

CREATE INDEX IF NOT EXISTS idx_online_feature_log_scored_at
    ON online_feature_log(scored_at);
