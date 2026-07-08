{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_user_login_stats.sql
-- Per-transaction rolling failed-login counts. Uses UNION ALL to fold login
-- events into the transaction stream, then window functions over the combined
-- event stream. The final SELECT keeps only rows anchored on transactions.

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

    sum(is_failed_login) OVER ({{ rolling_window('user_id', 'DAY', 1) }})   AS user_failed_logins_1d,
    sum(is_failed_login) OVER ({{ rolling_window('user_id', 'DAY', 7) }})   AS user_failed_logins_7d

FROM events
WHERE transaction_id != ''

