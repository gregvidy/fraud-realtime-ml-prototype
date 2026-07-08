{{ config(
    materialized='table',
    engine='MergeTree()',
    order_by='(user_id, event_timestamp, transaction_id)',
    partition_by='toYYYYMM(event_timestamp)'
) }}

-- fct_user_features.sql
-- Final user feature table keyed by (user_id, event_timestamp).
-- Feast-compatible: entity_key=user_id, timestamp=event_timestamp.

WITH txns              AS (SELECT transaction_id, user_id, event_timestamp FROM {{ ref('stg_transactions') }}),
     users             AS (SELECT user_id, account_type, is_verified, signup_date FROM {{ ref('stg_users') }}),
     user_txn_stats    AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_user_txn_stats') }}),
     user_login_stats  AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_user_login_stats') }}),
     user_txn_online   AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_user_txn_online_stats') }}),
     user_login_online AS (SELECT * EXCEPT event_timestamp FROM {{ ref('int_user_login_online_stats') }})

SELECT
    t.transaction_id AS transaction_id,
    t.user_id        AS user_id,
    t.event_timestamp AS event_timestamp,

    -- Account profile
    dateDiff('day', u.signup_date, t.event_timestamp)   AS user_account_age_days,
    CAST(u.account_type = 'standard' AS Int32)          AS user_is_standard_account,
    CAST(u.is_verified                AS Int32)         AS user_is_verified,

    -- Transaction velocity (batch)
    uts.user_txn_count_1d,
    uts.user_txn_count_7d,
    uts.user_txn_count_30d,
    uts.user_txn_amount_sum_1d,
    uts.user_txn_amount_sum_7d,
    uts.user_txn_amount_sum_30d,
    uts.user_avg_ticket_30d,
    uts.user_distinct_merchants_30d,
    uts.user_distinct_devices_30d,
    uts.user_decline_count_7d,

    -- Login risk (batch)
    uls.user_failed_logins_7d,
    uls.user_failed_logins_1d,

    -- Transaction velocity (online, mirrors Redis)
    COALESCE(uto.user_txn_count_5m, 0)              AS user_txn_count_5m,
    COALESCE(uto.user_txn_count_10m, 0)             AS user_txn_count_10m,
    COALESCE(uto.user_txn_count_1h, 0)              AS user_txn_count_1h,
    COALESCE(uto.user_txn_amount_sum_5m, 0)         AS user_txn_amount_sum_5m,
    COALESCE(uto.user_txn_amount_sum_10m, 0)        AS user_txn_amount_sum_10m,
    COALESCE(uto.user_txn_amount_sum_1h, 0)         AS user_txn_amount_sum_1h,
    COALESCE(uto.user_distinct_merchants_5m, 0)     AS user_distinct_merchants_5m,
    COALESCE(uto.user_distinct_merchants_10m, 0)    AS user_distinct_merchants_10m,
    COALESCE(uto.user_distinct_merchants_1h, 0)     AS user_distinct_merchants_1h,

    -- Login risk (online, mirrors Redis)
    COALESCE(ulo.user_failed_logins_15m, 0)         AS user_failed_logins_15m,
    COALESCE(ulo.user_failed_logins_1h, 0)          AS user_failed_logins_1h

FROM txns t
LEFT JOIN users u                    ON u.user_id = t.user_id
LEFT JOIN user_txn_stats       uts   USING (transaction_id)
LEFT JOIN user_login_stats     uls   USING (transaction_id)
LEFT JOIN user_txn_online      uto   USING (transaction_id)
LEFT JOIN user_login_online    ulo   USING (transaction_id)

