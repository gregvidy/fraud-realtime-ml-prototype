#!/usr/bin/env bash
# ============================================================================
# ClickHouse initial bootstrap — profiles, quotas, users, grants
#
# Runs once on first container start (docker-entrypoint-initdb.d). Uses shell
# env-var substitution so POC passwords come from CLICKHOUSE_*_PASSWORD.
#
# We use SQL access management (writable `local_directory` storage) rather
# than users_xml because users_xml storage is read-only from SQL — GRANT
# statements against XML-defined users fail with ACCESS_STORAGE_READONLY.
#
# This file is POC-grade. Production should:
#   - use IDENTIFIED WITH sha256_password BY '<pre-hashed>' (not plaintext)
#   - source passwords from a secrets store, not env vars
#   - restrict HOST ANY to specific CIDRs
# ============================================================================
set -eu

: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD (admin) must be set}"
: "${CLICKHOUSE_ANALYST_PASSWORD:?CLICKHOUSE_ANALYST_PASSWORD must be set}"
: "${CLICKHOUSE_BI_PASSWORD:?CLICKHOUSE_BI_PASSWORD must be set}"
: "${CLICKHOUSE_DS_PASSWORD:?CLICKHOUSE_DS_PASSWORD must be set}"
: "${CLICKHOUSE_SW_PASSWORD:?CLICKHOUSE_SW_PASSWORD must be set}"

clickhouse-client --host 127.0.0.1 --user default --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
-- ── Profiles ───────────────────────────────────────────────────────────
CREATE SETTINGS PROFILE IF NOT EXISTS analyst_profile SETTINGS
    readonly = 1,
    max_execution_time = 60,
    max_memory_usage = 4000000000;

CREATE SETTINGS PROFILE IF NOT EXISTS bi_profile SETTINGS
    readonly = 1,
    max_execution_time = 30,
    max_memory_usage = 2000000000;

CREATE SETTINGS PROFILE IF NOT EXISTS data_scientist_profile SETTINGS
    readonly = 0,
    max_execution_time = 3600,
    max_memory_usage = 16000000000;

CREATE SETTINGS PROFILE IF NOT EXISTS service_writer_profile SETTINGS
    readonly = 0,
    max_execution_time = 3600,
    max_memory_usage = 14000000000;

-- ── Quotas ─────────────────────────────────────────────────────────────
CREATE QUOTA IF NOT EXISTS bi_quota
    FOR INTERVAL 1 hour
        MAX queries = 10000, errors = 1000, result_rows = 1000000000,
            read_rows = 10000000000, execution_time = 7200;

-- ── Users ──────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS analyst
    IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_ANALYST_PASSWORD}'
    HOST ANY
    SETTINGS PROFILE 'analyst_profile';

CREATE USER IF NOT EXISTS bi_dashboard
    IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_BI_PASSWORD}'
    HOST ANY
    SETTINGS PROFILE 'bi_profile';

CREATE USER IF NOT EXISTS data_scientist
    IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_DS_PASSWORD}'
    HOST ANY
    SETTINGS PROFILE 'data_scientist_profile';

CREATE USER IF NOT EXISTS service_writer
    IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_SW_PASSWORD}'
    HOST ANY
    SETTINGS PROFILE 'service_writer_profile';

-- ── Quota assignment (SQL access management uses ALTER QUOTA TO <user>) ─
ALTER QUOTA bi_quota TO bi_dashboard;

-- ── Grants ─────────────────────────────────────────────────────────────
GRANT SELECT ON main.* TO analyst;

GRANT SELECT ON main.* TO bi_dashboard;

GRANT SELECT ON main.* TO data_scientist;
GRANT SELECT, INSERT, ALTER, CREATE, DROP, TRUNCATE ON sandbox.* TO data_scientist;

GRANT INSERT, SELECT ON raw.* TO service_writer;
GRANT INSERT, SELECT, ALTER, CREATE, DROP, TRUNCATE ON main.* TO service_writer;
SQL

echo "ClickHouse RBAC bootstrap complete."
