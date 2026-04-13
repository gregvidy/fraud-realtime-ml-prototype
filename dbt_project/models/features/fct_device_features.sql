-- fct_device_features.sql
-- Final device feature table keyed by (device_id, event_timestamp).
-- Feast-compatible: entity_key=device_id, timestamp=event_timestamp.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

device_stats AS (
    SELECT * FROM {{ ref('int_device_stats') }}
),

device_txn_online AS (
    SELECT * FROM {{ ref('int_device_txn_online_stats') }}
),

final AS (
    SELECT
        t.transaction_id,
        t.device_id,
        t.event_timestamp,

        -- Batch features
        ds.device_distinct_users_30d,
        ds.device_txn_count_7d,
        ds.device_txn_count_1d,

        -- Shared device risk flag
        (ds.device_distinct_users_30d > 2)::INT    AS device_is_shared_flag,

        -- Online features (mirrors Redis)
        COALESCE(dto.device_txn_count_5m, 0)       AS device_txn_count_5m,
        COALESCE(dto.device_txn_count_10m, 0)      AS device_txn_count_10m,
        COALESCE(dto.device_txn_count_1h, 0)       AS device_txn_count_1h

    FROM txns t
    LEFT JOIN device_stats     ds  ON ds.transaction_id  = t.transaction_id
    LEFT JOIN device_txn_online dto ON dto.transaction_id = t.transaction_id
)

SELECT * FROM final
