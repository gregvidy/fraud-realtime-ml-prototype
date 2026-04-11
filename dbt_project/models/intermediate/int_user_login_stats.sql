-- int_user_login_stats.sql
-- Per-user failed login counts over rolling windows, computed per transaction.

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

login_stats AS (
    SELECT
        t.transaction_id,
        t.user_id,
        t.event_timestamp,

        COUNT(*) FILTER (
            WHERE l.login_status = 'failed'
              AND l.event_timestamp >= t.event_timestamp - INTERVAL '7 days'
              AND l.event_timestamp <  t.event_timestamp
        )                           AS user_failed_logins_7d,

        COUNT(*) FILTER (
            WHERE l.login_status = 'failed'
              AND l.event_timestamp >= t.event_timestamp - INTERVAL '1 day'
              AND l.event_timestamp <  t.event_timestamp
        )                           AS user_failed_logins_1d

    FROM txns t
    LEFT JOIN logins l ON l.user_id = t.user_id
    GROUP BY t.transaction_id, t.user_id, t.event_timestamp
)

SELECT * FROM login_stats
