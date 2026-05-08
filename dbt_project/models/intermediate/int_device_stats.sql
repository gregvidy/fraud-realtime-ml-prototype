{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_device_stats.sql
-- Per-device rolling window stats (distinct users, transaction velocity).
-- Uses DuckDB RANGE window frames to avoid expensive self-joins.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

windowed AS (
    SELECT
        transaction_id,
        device_id,
        event_timestamp,

        COALESCE(LENGTH(LIST_DISTINCT(LIST(user_id) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0)                     AS device_distinct_users_30d,

        COUNT(*) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        )                           AS device_txn_count_7d,

        COUNT(*) OVER (
            PARTITION BY device_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 day' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        )                           AS device_txn_count_1d

    FROM txns
)

SELECT * FROM windowed
{% if is_incremental() %}
WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}
