-- stg_transactions.sql
-- Cleans and casts raw transactions. One row per transaction.
-- Composite indexes on (user_id, event_timestamp), (device_id, event_timestamp),
-- and (merchant_id, event_timestamp) are created via post-hook to speed up the
-- self-joins in intermediate models.
{{
    config(
        post_hook=[
            "CREATE INDEX IF NOT EXISTS idx_stg_txn_user_ts     ON {{ this }} (user_id, event_timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_stg_txn_device_ts   ON {{ this }} (device_id, event_timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_stg_txn_merchant_ts ON {{ this }} (merchant_id, event_timestamp)"
        ]
    )
}}

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
