{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(device_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_device_txn_online_stats.sql
-- Per-device SHORT-window transaction counts (batch mirror of Redis
-- device_txn_zset windows).

SELECT
    transaction_id,
    device_id,
    event_timestamp,

    COUNT(*) OVER ({{ rolling_window('device_id', 'MINUTE',  5) }})   AS device_txn_count_5m,
    COUNT(*) OVER ({{ rolling_window('device_id', 'MINUTE', 10) }})   AS device_txn_count_10m,
    COUNT(*) OVER ({{ rolling_window('device_id', 'HOUR',    1) }})   AS device_txn_count_1h

FROM {{ ref('stg_transactions') }}

