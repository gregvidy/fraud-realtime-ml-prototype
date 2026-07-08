-- stg_fraud_labels.sql
{{ config(materialized='table', engine='MergeTree()', order_by='(transaction_id)') }}

SELECT
    transaction_id,
    COALESCE(is_fraud, false)               AS is_fraud,
    fraud_type,
    label_source,
    label_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'fraud_labels') }}
WHERE transaction_id IS NOT NULL
