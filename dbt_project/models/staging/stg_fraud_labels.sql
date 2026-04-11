-- stg_fraud_labels.sql

SELECT
    transaction_id,
    COALESCE(is_fraud, false)               AS is_fraud,
    fraud_type,
    label_source,
    label_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'fraud_labels') }}
WHERE transaction_id IS NOT NULL