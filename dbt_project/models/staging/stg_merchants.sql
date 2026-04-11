-- stg_merchants.sql
-- One row per merchant.

SELECT
    merchant_id,
    merchant_name,
    LOWER(TRIM(merchant_category))      AS merchant_category,
    UPPER(TRIM(country_code))           AS country_code,
    COALESCE(is_online, false)          AS is_online,
    LOWER(TRIM(risk_tier))              AS risk_tier,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_merchants') }}
WHERE merchant_id IS NOT NULL
