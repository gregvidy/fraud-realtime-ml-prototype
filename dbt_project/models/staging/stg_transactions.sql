-- stg_transactions.sql
-- Cleans and casts raw transactions. One row per transaction.
-- Sort key = (user_id, event_timestamp, transaction_id) accelerates the
-- rolling-window computations in intermediate models.
{{
    config(
        materialized='table',
        engine='MergeTree()',
        order_by='(user_id, event_timestamp, transaction_id)',
        partition_by='toYYYYMM(event_timestamp)'
    )
}}

SELECT
    transaction_id,
    user_id,
    device_id,
    merchant_id,
    CAST(COALESCE(amount, 0) AS NUMERIC(18, 4))                     AS amount,
    UPPER(TRIM(currency))                                            AS currency,
    LOWER(TRIM(payment_method))                                      AS payment_method,
    UPPER(TRIM(country_code))                                        AS country_code,
    CAST(ip_address AS TEXT)                                         AS ip_address,
    COALESCE(is_international, false)                                AS is_international,
    LOWER(TRIM(txn_status))                                          AS txn_status,
    decline_reason,
    COALESCE(local_hour, CAST(EXTRACT(HOUR FROM event_timestamp) AS INT))  AS local_hour,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_transactions') }}
WHERE transaction_id IS NOT NULL
  AND amount > 0

