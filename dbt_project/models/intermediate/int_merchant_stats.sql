{{ config(
    materialized='incremental',
    unique_key='transaction_id'
) }}

-- int_merchant_stats.sql
-- Per-merchant rolling window stats (fraud rate, volume, avg ticket).
-- Uses DuckDB RANGE window frames to avoid expensive self-joins.

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
        transaction_id,
        merchant_id,
        event_timestamp,

        COUNT(*) OVER w               AS merchant_txn_count_30d,

        COALESCE(
            AVG(amount::FLOAT) OVER w, 0
        )                              AS merchant_avg_ticket_30d,

        COALESCE(
            AVG(is_fraud::INT::FLOAT) OVER w, 0
        )                              AS merchant_fraud_rate_30d

    FROM txns_with_label
    WINDOW w AS (
        PARTITION BY merchant_id
        ORDER BY event_timestamp
        RANGE BETWEEN INTERVAL '30 days' PRECEDING
                  AND INTERVAL '1 microsecond' PRECEDING
    )
)

SELECT * FROM merchant_stats
{% if is_incremental() %}
WHERE event_timestamp > (SELECT MAX(event_timestamp) FROM {{ this }})
{% endif %}
