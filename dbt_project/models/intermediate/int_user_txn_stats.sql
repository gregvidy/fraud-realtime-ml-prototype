{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_user_txn_stats.sql
-- Per-user rolling-window transaction stats, computed as of each transaction's
-- event_timestamp.
--
-- ClickHouse rewrite: window functions with `toUnixTimestamp(event_timestamp)`
-- as the numeric ORDER BY and second-granularity RANGE offsets. See
-- macros/time_window.sql for the rationale.

SELECT
    transaction_id,
    user_id,
    event_timestamp,

    -- counts
    COUNT(*) OVER ({{ rolling_window('user_id', 'DAY',  1) }})   AS user_txn_count_1d,
    COUNT(*) OVER ({{ rolling_window('user_id', 'DAY',  7) }})   AS user_txn_count_7d,
    COUNT(*) OVER ({{ rolling_window('user_id', 'DAY', 30) }})   AS user_txn_count_30d,

    -- amount sums (cast Decimal to Float64 for downstream Feast/parquet)
    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'DAY',  1) }})  AS user_txn_amount_sum_1d,
    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'DAY',  7) }})  AS user_txn_amount_sum_7d,
    sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'DAY', 30) }})  AS user_txn_amount_sum_30d,

    -- avg ticket over 30d (guard divide-by-zero)
    if(COUNT(*) OVER ({{ rolling_window('user_id', 'DAY', 30) }}) > 0,
       sum(toFloat64(amount)) OVER ({{ rolling_window('user_id', 'DAY', 30) }})
         / COUNT(*) OVER ({{ rolling_window('user_id', 'DAY', 30) }}),
       0.0)                                                                    AS user_avg_ticket_30d,

    -- distinct
    uniqExact(merchant_id) OVER ({{ rolling_window('user_id', 'DAY', 30) }})   AS user_distinct_merchants_30d,
    uniqExact(device_id)   OVER ({{ rolling_window('user_id', 'DAY', 30) }})   AS user_distinct_devices_30d,

    -- declines
    countIf(txn_status = 'decline') OVER ({{ rolling_window('user_id', 'DAY', 7) }})
                                                                                AS user_decline_count_7d

FROM {{ ref('stg_transactions') }}

