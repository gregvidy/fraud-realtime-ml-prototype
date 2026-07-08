{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_user_login_online_stats.sql
-- Per-transaction failed-login counts over SHORT windows (15m/1h) — batch
-- mirror of Redis user_login_fail_zset. Same UNION-ALL + window pattern as
-- int_user_login_stats, with sub-hour windows.

WITH events AS (
    SELECT transaction_id, user_id, event_timestamp, 0 AS is_failed_login
    FROM {{ ref('stg_transactions') }}
    UNION ALL
    SELECT '' AS transaction_id, user_id, event_timestamp, 1 AS is_failed_login
    FROM {{ ref('stg_login_events') }}
    WHERE login_status = 'failed'
)

SELECT
    transaction_id,
    user_id,
    event_timestamp,

    sum(is_failed_login) OVER ({{ rolling_window('user_id', 'MINUTE', 15) }})   AS user_failed_logins_15m,
    sum(is_failed_login) OVER ({{ rolling_window('user_id', 'HOUR',    1) }})   AS user_failed_logins_1h

FROM events
WHERE transaction_id != ''

