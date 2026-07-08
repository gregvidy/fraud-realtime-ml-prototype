{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(merchant_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- int_merchant_stats.sql
-- Per-merchant rolling window stats (fraud rate, volume, avg ticket).
-- Pre-joins fraud labels so fraud rate is a filtered avg over the window.

WITH t AS (
    SELECT
        p.transaction_id,
        p.merchant_id,
        p.event_timestamp,
        p.amount,
        COALESCE(l.is_fraud, 0) AS is_fraud
    FROM {{ ref('stg_transactions') }} AS p
    LEFT JOIN {{ ref('stg_fraud_labels') }} AS l USING (transaction_id)
)

SELECT
    transaction_id,
    merchant_id,
    event_timestamp,

    COUNT(*) OVER ({{ rolling_window('merchant_id', 'DAY', 30) }})                  AS merchant_txn_count_30d,

    if(COUNT(*) OVER ({{ rolling_window('merchant_id', 'DAY', 30) }}) > 0,
       avg(toFloat64(amount)) OVER ({{ rolling_window('merchant_id', 'DAY', 30) }}),
       0.0)                                                                         AS merchant_avg_ticket_30d,

    if(COUNT(*) OVER ({{ rolling_window('merchant_id', 'DAY', 30) }}) > 0,
       avg(toFloat64(is_fraud)) OVER ({{ rolling_window('merchant_id', 'DAY', 30) }}),
       0.0)                                                                         AS merchant_fraud_rate_30d

FROM t

