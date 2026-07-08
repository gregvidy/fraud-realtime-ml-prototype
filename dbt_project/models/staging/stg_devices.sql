-- stg_devices.sql
-- Cleans device registration events. One row per (device_id, user_id) event.
{{
    config(
        materialized='table',
        engine='MergeTree()',
        order_by='(user_id, event_timestamp, device_id)',
        partition_by='toYYYYMM(event_timestamp)'
    )
}}

SELECT
    device_event_id,
    device_id,
    user_id,
    device_fingerprint,
    platform,
    os_version,
    CAST(ip_address AS TEXT)                AS ip_address,
    UPPER(TRIM(country_code))               AS country_code,
    event_timestamp,
    ingestion_timestamp
FROM {{ source('raw', 'raw_devices') }}
WHERE device_id IS NOT NULL
  AND user_id   IS NOT NULL

