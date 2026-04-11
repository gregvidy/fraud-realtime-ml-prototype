-- fct_training_dataset.sql
-- Unified labelled dataset for model training.
-- Joins all feature tables on transaction_id plus fraud label.

WITH user_feat AS (
    SELECT * FROM {{ ref('fct_user_features') }}
),

device_feat AS (
    SELECT * FROM {{ ref('fct_device_features') }}
),

merchant_feat AS (
    SELECT * FROM {{ ref('fct_merchant_features') }}
),

txns AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

labels AS (
    SELECT * FROM {{ ref('stg_fraud_labels') }}
),

final AS (
    SELECT
        t.transaction_id,
        t.user_id,
        t.device_id,
        t.merchant_id,
        t.event_timestamp,

        -- Request-time features
        t.amount                                AS txn_amount,
        t.currency,
        t.payment_method,
        t.is_international::INT                 AS is_international,
        t.local_hour,
        (t.txn_status = 'decline')::INT         AS prev_decline_flag,

        -- User features
        uf.user_account_age_days,
        uf.user_is_verified,
        uf.user_is_standard_account,
        uf.user_txn_count_1d,
        uf.user_txn_count_7d,
        uf.user_txn_count_30d,
        uf.user_txn_amount_sum_1d,
        uf.user_txn_amount_sum_7d,
        uf.user_txn_amount_sum_30d,
        uf.user_avg_ticket_30d,
        uf.user_distinct_merchants_30d,
        uf.user_distinct_devices_30d,
        uf.user_decline_count_7d,
        uf.user_failed_logins_7d,
        uf.user_failed_logins_1d,

        -- Device features
        df.device_distinct_users_30d,
        df.device_txn_count_7d,
        df.device_txn_count_1d,
        df.device_is_shared_flag,

        -- Merchant features
        mf.merchant_is_high_risk,
        mf.merchant_is_online,
        mf.merchant_txn_count_30d,
        mf.merchant_avg_ticket_30d,
        mf.merchant_fraud_rate_30d,

        -- Label
        COALESCE(l.is_fraud, false)::INT        AS is_fraud

    FROM txns t
    LEFT JOIN user_feat     uf  ON uf.transaction_id  = t.transaction_id
    LEFT JOIN device_feat   df  ON df.transaction_id  = t.transaction_id
    LEFT JOIN merchant_feat mf  ON mf.transaction_id  = t.transaction_id
    LEFT JOIN labels        l   ON l.transaction_id   = t.transaction_id
)

SELECT * FROM final
