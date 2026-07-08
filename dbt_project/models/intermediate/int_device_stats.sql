{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(device_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_device_stats.sql
-- Per-device rolling window stats (distinct users, transaction velocity).
-- ClickHouse window-function pattern.

SELECT
    transaction_id,
    device_id,
    event_timestamp,

    uniqExact(user_id) OVER ({{ rolling_window('device_id', 'DAY', 30) }})   AS device_distinct_users_30d,

    COUNT(*) OVER ({{ rolling_window('device_id', 'DAY', 7) }})              AS device_txn_count_7d,
    COUNT(*) OVER ({{ rolling_window('device_id', 'DAY', 1) }})              AS device_txn_count_1d

FROM {{ ref('stg_transactions') }}

