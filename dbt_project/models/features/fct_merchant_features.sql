-- fct_merchant_features.sql
-- Final merchant feature table keyed by (merchant_id, event_timestamp).
-- Feast-compatible: entity_key=merchant_id, timestamp=event_timestamp.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

merchants AS (
    SELECT * FROM {{ ref('stg_merchants') }}
),

merchant_stats AS (
    SELECT * FROM {{ ref('int_merchant_stats') }}
),

final AS (
    SELECT
        t.transaction_id,
        t.merchant_id,
        t.event_timestamp,

        m.merchant_category,
        m.risk_tier,
        (m.risk_tier = 'high')::INT         AS merchant_is_high_risk,
        m.is_online::INT                    AS merchant_is_online,

        ms.merchant_txn_count_30d,
        ms.merchant_avg_ticket_30d,
        ms.merchant_fraud_rate_30d

    FROM txns t
    LEFT JOIN merchants m     ON m.merchant_id = t.merchant_id
    LEFT JOIN merchant_stats ms ON ms.transaction_id = t.transaction_id
)

SELECT * FROM final
