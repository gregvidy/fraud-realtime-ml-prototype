-- stg_transactions.sql
-- Cleans and casts raw transactions. One row per transaction.

SELECT
    transaction_id,
    user_id,
    device_id,
    merchant_id,
    COALESCE(amount, 0)::NUMERIC(18, 4)        AS amount,
    UPPER(TRIM(currency))                       AS currency,
    LOWER(TRIM(payment_method))                 AS payment_method,
    UPPER(TRIM(country_code))                   AS country_code,
    ip_address::TEXT                            AS ip_address,
    COALESCE(is_international, false)           AS is_international,
    LOWER(TRIM(txn_status))                     AS txn_status,
    decline_reason,
    COALESCE(local_hour, EXTRACT(HOUR FROM event_timestamp)::INT) AS local_hour,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_transactions') }}
WHERE transaction_id IS NOT NULL
  AND amount > 0
