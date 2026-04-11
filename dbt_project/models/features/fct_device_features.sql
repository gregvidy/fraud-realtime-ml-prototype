-- fct_device_features.sql
-- Final device feature table keyed by (device_id, event_timestamp).
-- Feast-compatible: entity_key=device_id, timestamp=event_timestamp.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

device_stats AS (
    SELECT * FROM {{ ref('int_device_stats') }}
),

final AS (
    SELECT
        t.transaction_id,
        t.device_id,
        t.event_timestamp,

        ds.device_distinct_users_30d,
        ds.device_txn_count_7d,
        ds.device_txn_count_1d,

        -- Shared device risk flag
        (ds.device_distinct_users_30d > 2)::INT AS device_is_shared_flag

    FROM txns t
    LEFT JOIN device_stats ds ON ds.transaction_id = t.transaction_id
)

SELECT * FROM final
