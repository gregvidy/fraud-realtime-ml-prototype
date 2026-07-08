-- stg_users.sql
-- Cleans raw_users. One row per user.
{{ config(materialized='table', engine='MergeTree()', order_by='(user_id)') }}

SELECT
    user_id,
    LOWER(TRIM(email))                          AS email,
    phone,
    UPPER(TRIM(country_code))                   AS country_code,
    CAST(signup_date AS DATE)                   AS signup_date,
    LOWER(TRIM(account_type))                   AS account_type,
    COALESCE(is_verified, false)                AS is_verified,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_users') }}
WHERE user_id IS NOT NULL

