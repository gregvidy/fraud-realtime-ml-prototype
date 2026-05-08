{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_user_txn_online_stats.sql
-- Per-user short-window transaction stats for training.
-- Mirrors Redis: user_txn_zset windows.
-- Uses DuckDB RANGE window frames to avoid expensive self-joins.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

windowed AS (
    SELECT
        transaction_id,
        user_id,
        event_timestamp,

        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_5m,

        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '10 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_10m,

        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_1h,

        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_5m,

        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '10 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_10m,

        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_1h,

        COALESCE(LENGTH(LIST_DISTINCT(LIST(merchant_id) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0) AS user_distinct_merchants_5m,

        COALESCE(LENGTH(LIST_DISTINCT(LIST(merchant_id) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '10 minutes' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0) AS user_distinct_merchants_10m,

        COALESCE(LENGTH(LIST_DISTINCT(LIST(merchant_id) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0) AS user_distinct_merchants_1h

    FROM txns
)

SELECT * FROM windowed
{% if is_incremental() %}
WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}