{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_user_login_online_stats.sql
-- Per-user short-window failed login count for training.
-- Mirrors Redis get_user_login_features(): user_login_fail_zset window 15m.
-- One row per (user_id, transaction_id) — point-in-time correct.

WITH logins AS (
    SELECT * FROM {{ ref('stg_login_events') }}
),

txns AS (
    SELECT
        transaction_id,
        user_id,
        event_timestamp
    FROM {{ ref('stg_transactions') }}
    {% if is_incremental() %}
    WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT
    t.transaction_id,
    t.user_id,
    t.event_timestamp,

    COUNT(*) FILTER (
        WHERE l.login_status = 'failed'
          AND l.event_timestamp >= t.event_timestamp - INTERVAL '15 minutes'
          AND l.event_timestamp <  t.event_timestamp
    ) AS user_failed_logins_15m,

    COUNT(*) FILTER (
        WHERE l.login_status = 'failed'
          AND l.event_timestamp >= t.event_timestamp - INTERVAL '1 hour'
          AND l.event_timestamp <  t.event_timestamp
    ) AS user_failed_logins_1h

FROM txns t
LEFT JOIN logins l ON l.user_id = t.user_id
GROUP BY t.transaction_id, t.user_id, t.event_timestamp
