{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_user_txn_stats.sql
-- Per-user rolling window transaction statistics, computed as of each
-- transaction's event_timestamp for point-in-time compatibility.
-- One row per (user_id, event_timestamp) — keyed on the anchor transaction.
-- Uses DuckDB RANGE window frames to avoid expensive self-joins.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

windowed AS (
    SELECT
        transaction_id,
        user_id,
        event_timestamp,

        -- counts
        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 day' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_1d,

        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_7d,

        COUNT(*) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ) AS user_txn_count_30d,

        -- amount sums
        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 day' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_1d,

        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_7d,

        COALESCE(SUM(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_txn_amount_sum_30d,

        -- avg ticket
        COALESCE(AVG(amount) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_avg_ticket_30d,

        -- distinct merchants
        COALESCE(LENGTH(LIST_DISTINCT(LIST(merchant_id) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0) AS user_distinct_merchants_30d,

        -- distinct devices
        COALESCE(LENGTH(LIST_DISTINCT(LIST(device_id) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ))), 0) AS user_distinct_devices_30d,

        -- declines
        COALESCE(SUM(CASE WHEN txn_status = 'decline' THEN 1 ELSE 0 END) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_decline_count_7d

    FROM txns
)

SELECT * FROM windowed
{% if is_incremental() %}
WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}
