{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_user_txn_online_stats.sql
-- Per-user SHORT-window transaction stats (5m/10m/1h) — batch mirror of
-- the Redis sliding-window features.

SELECT
    transaction_id,
    user_id,
    event_timestamp,

    COUNT(*) OVER ({{ rolling_window('user_id', 'MINUTE',  5) }})   AS user_txn_count_5m,
    COUNT(*) OVER ({{ rolling_window('user_id', 'MINUTE', 10) }})   AS user_txn_count_10m,
    COUNT(*) OVER ({{ rolling_window('user_id', 'HOUR',    1) }})   AS user_txn_count_1h,

    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'MINUTE',  5) }})  AS user_txn_amount_sum_5m,
    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'MINUTE', 10) }})  AS user_txn_amount_sum_10m,
    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'HOUR',    1) }})  AS user_txn_amount_sum_1h,

    uniqExact(merchant_id) OVER ({{ rolling_window('user_id', 'MINUTE',  5) }})  AS user_distinct_merchants_5m,
    uniqExact(merchant_id) OVER ({{ rolling_window('user_id', 'MINUTE', 10) }})  AS user_distinct_merchants_10m,
    uniqExact(merchant_id) OVER ({{ rolling_window('user_id', 'HOUR',    1) }})  AS user_distinct_merchants_1h

FROM {{ ref('stg_transactions') }}
