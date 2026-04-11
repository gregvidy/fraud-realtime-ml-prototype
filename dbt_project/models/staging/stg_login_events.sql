-- stg_login_events.sql
-- Cleaned login events.

SELECT
    login_event_id,
    user_id,
    device_id,
    ip_address::TEXT                        AS ip_address,
    UPPER(TRIM(country_code))               AS country_code,
    LOWER(TRIM(login_status))               AS login_status,
    failure_reason,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_login_events') }}
WHERE user_id IS NOT NULL
