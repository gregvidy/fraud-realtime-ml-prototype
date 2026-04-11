-- stg_devices.sql
-- Cleans device registration events. One row per (device_id, user_id) event.

SELECT
    device_event_id,
    device_id,
    user_id,
    device_fingerprint,
    platform,
    os_version,
    ip_address::TEXT                        AS ip_address,
    UPPER(TRIM(country_code))               AS country_code,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_devices') }}
WHERE device_id IS NOT NULL
  AND user_id   IS NOT NULL
