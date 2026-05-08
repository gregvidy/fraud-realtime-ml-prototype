{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_device_txn_online_stats.sql
-- Per-device short-window transaction counts for training.
-- Mirrors Redis get_device_online_features(): device_txn_zset windows 5m and 10m.
-- One row per (device_id, transaction_id) — point-in-time correct.
-- Uses DuckDB RANGE window frames to avoid expensive self-joins.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

windowed AS (
    SELECT
        transaction_id,
        device_id,
        event_timestamp,

        COUNT(*) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS device_txn_count_5m,

        COUNT(*) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '10 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS device_txn_count_10m,

        COUNT(*) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS device_txn_count_1h

    FROM txns
)

SELECT * FROM windowed
{% if is_incremental() %}
WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}
