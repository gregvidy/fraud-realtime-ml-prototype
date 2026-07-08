{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(merchant_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- fct_merchant_features.sql
-- Final merchant feature table keyed by (merchant_id, event_timestamp).
-- Feast-compatible: entity_key=merchant_id, timestamp=event_timestamp.

WITH txns           AS (SELECT transaction_id, merchant_id, event_timestamp FROM {{ ref('stg_transactions') }}),
     merchants      AS (SELECT merchant_id, merchant_category, risk_tier, is_online FROM {{ ref('stg_merchants') }}),
     merchant_stats AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_merchant_stats') }})

SELECT
    t.transaction_id AS transaction_id,
    t.merchant_id    AS merchant_id,
    t.event_timestamp AS event_timestamp,

    m.merchant_category,
    m.risk_tier,
    CAST(m.risk_tier = 'high' AS Int32)       AS merchant_is_high_risk,
    CAST(m.is_online          AS Int32)       AS merchant_is_online,

    ms.merchant_txn_count_30d,
    ms.merchant_avg_ticket_30d,
    ms.merchant_fraud_rate_30d

FROM txns t
LEFT JOIN merchants      m  ON m.merchant_id = t.merchant_id
LEFT JOIN merchant_stats ms USING (transaction_id)

