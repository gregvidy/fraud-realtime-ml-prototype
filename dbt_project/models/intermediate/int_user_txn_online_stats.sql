WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
)

-- mirrors Redis: user_txn_zset windows
SELECT
    t.transaction_id,
    t.user_id,
    t.event_timestamp,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '5 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_txn_count_5m,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '10 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_txn_count_10m,

    COUNT(*) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 hour'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_txn_count_1h,

    COALESCE(SUM(h.amount) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '5 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ), 0) AS user_txn_amount_sum_5m,

    COALESCE(SUM(h.amount) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '10 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ), 0) AS user_txn_amount_sum_10m,

    COALESCE(SUM(h.amount) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 hour'
          AND h.event_timestamp <  t.event_timestamp
    ), 0) AS user_txn_amount_sum_1h,

    COUNT(DISTINCT h.merchant_id) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '5 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_distinct_merchants_5m,

    COUNT(DISTINCT h.merchant_id) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '10 minutes'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_distinct_merchants_10m,

    COUNT(DISTINCT h.merchant_id) FILTER (
        WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '1 hour'
          AND h.event_timestamp <  t.event_timestamp
    ) AS user_distinct_merchants_1h

FROM txns t
JOIN txns h ON h.user_id = t.user_id
GROUP BY t.transaction_id, t.user_id, t.event_timestamp