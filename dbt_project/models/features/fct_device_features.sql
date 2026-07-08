{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(device_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- fct_device_features.sql
-- Final device feature table keyed by (device_id, event_timestamp).
-- Feast-compatible: entity_key=device_id, timestamp=event_timestamp.

WITH txns              AS (SELECT transaction_id, device_id, event_timestamp FROM {{ ref('stg_transactions') }}),
     device_stats      AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_device_stats') }}),
     device_txn_online AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_device_txn_online_stats') }})

SELECT
    t.transaction_id AS transaction_id,
    t.device_id      AS device_id,
    t.event_timestamp AS event_timestamp,

    -- Batch features
    ds.device_distinct_users_30d,
    ds.device_txn_count_7d,
    ds.device_txn_count_1d,

    -- Shared device risk flag
    CAST(ds.device_distinct_users_30d > 2 AS Int32)   AS device_is_shared_flag,

    -- Online features (mirrors Redis)
    COALESCE(dto.device_txn_count_5m, 0)              AS device_txn_count_5m,
    COALESCE(dto.device_txn_count_10m, 0)             AS device_txn_count_10m,
    COALESCE(dto.device_txn_count_1h, 0)              AS device_txn_count_1h

FROM txns t
LEFT JOIN device_stats     ds  USING (transaction_id)
LEFT JOIN device_txn_online dto USING (transaction_id)

