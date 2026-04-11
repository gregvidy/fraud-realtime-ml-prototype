-- int_user_txn_stats.sql
-- Per-user rolling window transaction statistics, computed as of each
-- transaction's event_timestamp for point-in-time compatibility.
-- One row per (user_id, event_timestamp) — keyed on the anchor transaction.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

user_stats AS (
    SELECT
        t.transaction_id,
        t.user_id,
        t.event_timestamp,

        -- counts
        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 day'
              AND h.event_timestamp <  t.event_timestamp
        ) AS user_txn_count_1d,

        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '7 days'
              AND h.event_timestamp <  t.event_timestamp
        ) AS user_txn_count_7d,

        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        ) AS user_txn_count_30d,

        -- amount sums
        COALESCE(SUM(h.amount) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 day'
              AND h.event_timestamp <  t.event_timestamp
        ), 0)                               AS user_txn_amount_sum_1d,

        COALESCE(SUM(h.amount) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '7 days'
              AND h.event_timestamp <  t.event_timestamp
        ), 0)                               AS user_txn_amount_sum_7d,

        COALESCE(SUM(h.amount) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        ), 0)                               AS user_txn_amount_sum_30d,

        -- avg ticket
        COALESCE(AVG(h.amount) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        ), 0)                               AS user_avg_ticket_30d,

        -- distinct merchants
        COUNT(DISTINCT h.merchant_id) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        )                                   AS user_distinct_merchants_30d,

        -- distinct devices
        COUNT(DISTINCT h.device_id) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        )                                   AS user_distinct_devices_30d,

        -- declines
        COUNT(*) FILTER (
            WHERE h.txn_status = 'decline'
              AND h.event_timestamp >= t.event_timestamp - INTERVAL '7 days'
              AND h.event_timestamp <  t.event_timestamp
        )                                   AS user_decline_count_7d

    FROM txns t
    JOIN txns h ON h.user_id = t.user_id
    GROUP BY
        t.transaction_id, t.user_id, t.event_timestamp
)

SELECT * FROM user_stats
