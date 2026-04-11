-- int_device_stats.sql
-- Per-device rolling window stats (distinct users, transaction velocity).

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

device_stats AS (
    SELECT
        t.transaction_id,
        t.device_id,
        t.event_timestamp,

        COUNT(DISTINCT h.user_id) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        )                           AS device_distinct_users_30d,

        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '7 days'
              AND h.event_timestamp <  t.event_timestamp
        )                           AS device_txn_count_7d,

        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 day'
              AND h.event_timestamp <  t.event_timestamp
        )                           AS device_txn_count_1d

    FROM txns t
    JOIN txns h ON h.device_id = t.device_id
    GROUP BY t.transaction_id, t.device_id, t.event_timestamp
)

SELECT * FROM device_stats
