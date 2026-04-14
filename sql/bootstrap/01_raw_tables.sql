-- =============================================================================
-- Bootstrap: Raw tables for Fraud Detection MVP
-- Schema: public
-- Run order: 01 (executed automatically on first Postgres start via initdb.d)
-- =============================================================================

-- raw_users: one row per registered user
CREATE TABLE IF NOT EXISTS raw_users (
    user_id             VARCHAR(64)     PRIMARY KEY,
    email               VARCHAR(255)    NOT NULL,
    phone               VARCHAR(32),
    country_code        CHAR(2)         NOT NULL,
    signup_date         DATE            NOT NULL,
    account_type        VARCHAR(32)     NOT NULL DEFAULT 'standard',
    is_verified         BOOLEAN         NOT NULL DEFAULT FALSE,
    event_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- raw_devices: one row per device registration event
CREATE TABLE IF NOT EXISTS raw_devices (
    device_event_id     BIGSERIAL       PRIMARY KEY,
    device_id           VARCHAR(64)     NOT NULL,
    user_id             VARCHAR(64)     NOT NULL,
    device_fingerprint  VARCHAR(128),
    platform            VARCHAR(32),
    os_version          VARCHAR(32),
    ip_address          INET,
    country_code        CHAR(2),
    event_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_devices_device_id ON raw_devices(device_id);
CREATE INDEX IF NOT EXISTS idx_raw_devices_user_id   ON raw_devices(user_id);

-- raw_merchants: one row per merchant
CREATE TABLE IF NOT EXISTS raw_merchants (
    merchant_id         VARCHAR(64)     PRIMARY KEY,
    merchant_name       VARCHAR(255)    NOT NULL,
    merchant_category   VARCHAR(64)     NOT NULL,
    country_code        CHAR(2)         NOT NULL,
    is_online           BOOLEAN         NOT NULL DEFAULT FALSE,
    risk_tier           VARCHAR(16)     NOT NULL DEFAULT 'medium',
    event_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- raw_transactions: one row per payment transaction
CREATE TABLE IF NOT EXISTS raw_transactions (
    transaction_id      VARCHAR(64)     PRIMARY KEY,
    user_id             VARCHAR(64)     NOT NULL,
    device_id           VARCHAR(64)     NOT NULL,
    merchant_id         VARCHAR(64)     NOT NULL,
    amount              NUMERIC(18, 4)  NOT NULL,
    currency            CHAR(3)         NOT NULL DEFAULT 'USD',
    payment_method      VARCHAR(32)     NOT NULL,
    country_code        CHAR(2)         NOT NULL,
    ip_address          INET,
    is_international    BOOLEAN         NOT NULL DEFAULT FALSE,
    txn_status          VARCHAR(16)     NOT NULL,
    decline_reason      VARCHAR(64),
    local_hour          SMALLINT,
    event_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_txn_user_id         ON raw_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_raw_txn_device_id       ON raw_transactions(device_id);
CREATE INDEX IF NOT EXISTS idx_raw_txn_merchant_id     ON raw_transactions(merchant_id);
CREATE INDEX IF NOT EXISTS idx_raw_txn_event_timestamp ON raw_transactions(event_timestamp);

-- raw_login_events: one row per login attempt
CREATE TABLE IF NOT EXISTS raw_login_events (
    login_event_id      BIGSERIAL       PRIMARY KEY,
    user_id             VARCHAR(64)     NOT NULL,
    device_id           VARCHAR(64),
    ip_address          INET,
    country_code        CHAR(2),
    login_status        VARCHAR(16)     NOT NULL,
    failure_reason      VARCHAR(64),
    event_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_login_user_id         ON raw_login_events(user_id);
CREATE INDEX IF NOT EXISTS idx_raw_login_event_timestamp ON raw_login_events(event_timestamp);

-- fraud_labels: authoritative fraud outcome per transaction
CREATE TABLE IF NOT EXISTS fraud_labels (
    transaction_id      VARCHAR(64)     PRIMARY KEY,
    is_fraud            BOOLEAN         NOT NULL DEFAULT FALSE,
    fraud_type          VARCHAR(64),
    label_source        VARCHAR(32)     NOT NULL DEFAULT 'synthetic',
    label_timestamp     TIMESTAMPTZ     NOT NULL,
    ingestion_timestamp TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- model_score_log: every inference result, used for model monitoring
CREATE TABLE IF NOT EXISTS model_score_log (
    id                      BIGSERIAL       PRIMARY KEY,
    transaction_id          VARCHAR(64)     NOT NULL,
    user_id                 VARCHAR(64)     NOT NULL,
    device_id               VARCHAR(64)     NOT NULL,
    merchant_id             VARCHAR(64)     NOT NULL,
    fraud_score             NUMERIC(8, 6)   NOT NULL,
    risk_band               VARCHAR(16)     NOT NULL,
    is_flagged              BOOLEAN         NOT NULL,
    model_version           VARCHAR(64)     NOT NULL,
    feature_service_version VARCHAR(64)     NOT NULL DEFAULT 'fraud_scoring_v1',
    feast_offline_ok        BOOLEAN         NOT NULL DEFAULT FALSE,
    redis_online_ok         BOOLEAN         NOT NULL DEFAULT FALSE,
    scored_at               TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_score_log_transaction_id ON model_score_log(transaction_id);
CREATE INDEX IF NOT EXISTS idx_score_log_user_id        ON model_score_log(user_id);
CREATE INDEX IF NOT EXISTS idx_score_log_scored_at      ON model_score_log(scored_at);
CREATE INDEX IF NOT EXISTS idx_score_log_model_version  ON model_score_log(model_version);
