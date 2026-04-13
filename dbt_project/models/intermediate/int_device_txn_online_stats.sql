{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_device_txn_online_stats.sql
-- Per-device short-window transaction counts for training.
-- Mirrors Redis get_device_online_features(): device_txn_zset windows 5m and 10m.
-- One row per (device_id, transaction_id) — point-in-time correct.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

{% if is_incremental() %}
anchor_txns AS (
    SELECT * FROM txns
    WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
)
{% else %}
anchor_txns AS (
    SELECT * FROM txns
)
{% endif %}

SELECT
    t.transaction_id,
    t.device_id,
    t.event_timestamp,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '5 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS device_txn_count_5m,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '10 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS device_txn_count_10m,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 hour'
          AND h.event_timestamp <  t.event_timestamp
    ) AS device_txn_count_1h

FROM anchor_txns t
JOIN txns h ON h.device_id = t.device_id
GROUP BY t.transaction_id, t.device_id, t.event_timestamp
