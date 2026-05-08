{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_user_login_stats.sql
-- Per-user failed login counts over rolling windows, computed per transaction.
-- Uses UNION ALL + RANGE window frames to avoid expensive cross-table joins.

WITH logins AS (
    SELECT * FROM {{ ref('stg_login_events') }}
),

txns AS (
    SELECT
        transaction_id,
        user_id,
        event_timestamp
    FROM {{ ref('stg_transactions') }}
),

events AS (
    SELECT transaction_id, user_id, event_timestamp, 0 AS is_failed_login
    FROM txns
    UNION ALL
    SELECT NULL AS transaction_id, user_id, event_timestamp, 1 AS is_failed_login
    FROM logins
    WHERE login_status = 'failed'
),

windowed AS (
    SELECT
        transaction_id,
        user_id,
        event_timestamp,

        COALESCE(SUM(is_failed_login) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_failed_logins_7d,

        COALESCE(SUM(is_failed_login) OVER (
            PARTITION BY user_id ORDER BY event_timestamp
            RANGE BETWEEN INTERVAL '1 day' PRECEDING AND INTERVAL '1 microsecond' PRECEDING
        ), 0) AS user_failed_logins_1d

    FROM events
)

SELECT * FROM windowed
WHERE transaction_id IS NOT NULL
{% if is_incremental() %}
  AND event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}
