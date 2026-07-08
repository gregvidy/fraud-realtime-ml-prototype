{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- fct_training_dataset.sql
-- Unified labelled dataset for model training.
-- Joins all feature tables on transaction_id plus fraud label.

WITH user_feat     AS (SELECT * EXCEPT (user_id, event_timestamp) FROM {{ ref('fct_user_features') }}),
     device_feat   AS (SELECT * EXCEPT (device_id, event_timestamp) FROM {{ ref('fct_device_features') }}),
     merchant_feat AS (SELECT * EXCEPT (merchant_id, event_timestamp) FROM {{ ref('fct_merchant_features') }}),
     txns          AS (SELECT * FROM {{ ref('stg_transactions') }}),
     labels        AS (SELECT transaction_id, is_fraud FROM {{ ref('stg_fraud_labels') }})

SELECT
    t.transaction_id AS transaction_id,
    t.user_id        AS user_id,
    t.device_id      AS device_id,
    t.merchant_id    AS merchant_id,
    t.event_timestamp AS event_timestamp,

    -- Request-time features
    t.amount                              AS txn_amount,
    t.currency,
    t.payment_method,
    CAST(t.is_international AS Int32)     AS is_international,
    t.local_hour,
    CAST(t.txn_status = 'decline' AS Int32) AS prev_decline_flag,

    -- User features
    uf.user_account_age_days,
    uf.user_is_verified,
    uf.user_is_standard_account,
    uf.user_txn_count_1d,
    uf.user_txn_count_7d,
    uf.user_txn_count_30d,
    uf.user_txn_amount_sum_1d,
    uf.user_txn_amount_sum_7d,
    uf.user_txn_amount_sum_30d,
    uf.user_avg_ticket_30d,
    uf.user_distinct_merchants_30d,
    uf.user_distinct_devices_30d,
    uf.user_decline_count_7d,
    uf.user_failed_logins_7d,
    uf.user_failed_logins_1d,

    -- Device features
    df.device_distinct_users_30d,
    df.device_txn_count_7d,
    df.device_txn_count_1d,
    df.device_is_shared_flag,

    -- Merchant features
    mf.merchant_is_high_risk,
    mf.merchant_is_online,
    mf.merchant_txn_count_30d,
    mf.merchant_avg_ticket_30d,
    mf.merchant_fraud_rate_30d,

    -- User online features (from fct_user_features)
    uf.user_txn_count_5m,
    uf.user_txn_count_10m,
    uf.user_txn_count_1h,
    uf.user_txn_amount_sum_5m,
    uf.user_txn_amount_sum_10m,
    uf.user_txn_amount_sum_1h,
    uf.user_distinct_merchants_5m,
    uf.user_distinct_merchants_10m,
    uf.user_distinct_merchants_1h,
    uf.user_failed_logins_15m,
    uf.user_failed_logins_1h,

    -- Device online features (from fct_device_features)
    df.device_txn_count_5m,
    df.device_txn_count_10m,
    df.device_txn_count_1h,

    -- Label
    CAST(COALESCE(l.is_fraud, 0) AS Int32) AS is_fraud

FROM txns t
LEFT JOIN user_feat     uf USING (transaction_id)
LEFT JOIN device_feat   df USING (transaction_id)
LEFT JOIN merchant_feat mf USING (transaction_id)
LEFT JOIN labels        l  USING (transaction_id)

