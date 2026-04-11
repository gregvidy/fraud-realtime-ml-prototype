-- int_merchant_stats.sql
-- Per-merchant rolling window stats (fraud rate, volume, avg ticket).

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

labels AS (
    SELECT * FROM {{ ref('stg_fraud_labels') }}
),

txns_with_label AS (
    SELECT
        t.*,
        COALESCE(l.is_fraud, false) AS is_fraud
    FROM txns t
    LEFT JOIN labels l USING (transaction_id)
),

merchant_stats AS (
    SELECT
        t.transaction_id,
        t.merchant_id,
        t.event_timestamp,

        COUNT(*) FILTER (
            WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
              AND h.event_timestamp <  t.event_timestamp
        )                               AS merchant_txn_count_30d,

        COALESCE(
            AVG(h.amount::FLOAT) FILTER (
                WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
                  AND h.event_timestamp <  t.event_timestamp
            ), 0
        )                               AS merchant_avg_ticket_30d,

        COALESCE(
            AVG(h.is_fraud::INT::FLOAT) FILTER (
                WHERE h.event_timestamp >= t.event_timestamp - INTERVAL '30 days'
                  AND h.event_timestamp <  t.event_timestamp
            ), 0
        )                               AS merchant_fraud_rate_30d

    FROM txns_with_label t
    JOIN txns_with_label h ON h.merchant_id = t.merchant_id
    GROUP BY t.transaction_id, t.merchant_id, t.event_timestamp
)

SELECT * FROM merchant_stats
