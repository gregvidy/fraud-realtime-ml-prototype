-- fct_user_features.sql
-- Final user feature table keyed by (user_id, event_timestamp).
-- Feast-compatible: entity_key=user_id, timestamp=event_timestamp.

WITH txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

users AS (
    SELECT * FROM {{ ref('stg_users') }}
),

user_txn_stats AS (
    SELECT * FROM {{ ref('int_user_txn_stats') }}
),

user_login_stats AS (
    SELECT * FROM {{ ref('int_user_login_stats') }}
),

final AS (
    SELECT
        t.transaction_id,
        t.user_id,
        t.event_timestamp,

        -- Account profile
        EXTRACT(DAY FROM (t.event_timestamp - u.signup_date::TIMESTAMPTZ))::INT
                                                AS user_account_age_days,
        (u.account_type = 'standard')::INT      AS user_is_standard_account,
        u.is_verified::INT                      AS user_is_verified,

        -- Transaction velocity
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

        -- Login risk
        uls.user_failed_logins_7d,
        uls.user_failed_logins_1d

    FROM txns t
    LEFT JOIN users         u   ON u.user_id = t.user_id
    LEFT JOIN user_txn_stats uts ON uts.transaction_id = t.transaction_id
    LEFT JOIN user_login_stats uls ON uls.transaction_id = t.transaction_id
)

SELECT * FROM final
