#!/usr/bin/env bash
# ============================================================================
# verify_clickhouse_rbac.sh
# ----------------------------------------------------------------------------
# Proves that the four POC RBAC roles defined in
# infra/clickhouse/users.d/roles.xml + infra/clickhouse/init.sql have the
# expected grants.
#
# Exit code 0 = all assertions passed.
# Exit code >0 = at least one assertion failed (details printed).
#
# Assumes the ClickHouse container is running and the init.sql grants have
# been applied. Called via `make ch-verify-rbac`.
# ============================================================================
set -u

CONTAINER="${CLICKHOUSE_CONTAINER:-fraud_clickhouse}"

# Load .env if present so passwords come from the same place as docker-compose.
if [[ -f .env ]]; then
    set -a; . ./.env; set +a
fi

ADMIN_PW="${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}"
ANALYST_PW="${CLICKHOUSE_ANALYST_PASSWORD:-analyst_pass}"
BI_PW="${CLICKHOUSE_BI_PASSWORD:-bi_pass}"
DS_PW="${CLICKHOUSE_DS_PASSWORD:-ds_pass}"
SW_PW="${CLICKHOUSE_SW_PASSWORD:-sw_pass}"

pass=0
fail=0

# Runs a query as a given user. Returns exit code from clickhouse-client.
# $1 = user, $2 = password, $3 = query
run_as() {
    docker exec "$CONTAINER" clickhouse-client \
        --user "$1" --password "$2" --query "$3" 2>&1
}

# Assert that a query SUCCEEDS. $1 label, $2 user, $3 password, $4 query
assert_ok() {
    local label="$1" user="$2" pw="$3" q="$4"
    local out
    if out=$(run_as "$user" "$pw" "$q"); then
        echo "  [PASS] $label"
        pass=$((pass + 1))
    else
        echo "  [FAIL] $label"
        echo "         query : $q"
        echo "         output: $out"
        fail=$((fail + 1))
    fi
}

# Assert that a query FAILS (permission denied etc.). $1 label, $2 user, $3 password, $4 query
assert_denied() {
    local label="$1" user="$2" pw="$3" q="$4"
    local out
    if out=$(run_as "$user" "$pw" "$q") ; then
        echo "  [FAIL] $label  (query unexpectedly succeeded)"
        echo "         query : $q"
        echo "         output: $out"
        fail=$((fail + 1))
    else
        echo "  [PASS] $label  (denied as expected)"
        pass=$((pass + 1))
    fi
}

# ----------------------------------------------------------------------------
# Setup — as admin, ensure a scratch table exists in each schema for tests.
# ----------------------------------------------------------------------------
echo "── Setup: creating scratch tables as admin ──"
run_as default "$ADMIN_PW" "
    CREATE TABLE IF NOT EXISTS raw._rbac_probe   (v UInt32) ENGINE = MergeTree ORDER BY v;
    CREATE TABLE IF NOT EXISTS main._rbac_probe  (v UInt32) ENGINE = MergeTree ORDER BY v;
    CREATE TABLE IF NOT EXISTS sandbox._rbac_probe (v UInt32) ENGINE = MergeTree ORDER BY v;
    INSERT INTO main._rbac_probe VALUES (1);
" > /dev/null

# ----------------------------------------------------------------------------
# analyst — SELECT on main.*, nothing else
# ----------------------------------------------------------------------------
echo "── analyst ──"
assert_ok      "analyst can connect"               analyst "$ANALYST_PW" "SELECT 1"
assert_ok      "analyst can SELECT main._rbac_probe" analyst "$ANALYST_PW" "SELECT count() FROM main._rbac_probe"
assert_denied  "analyst CANNOT INSERT into main"   analyst "$ANALYST_PW" "INSERT INTO main._rbac_probe VALUES (2)"
assert_denied  "analyst CANNOT SELECT from sandbox" analyst "$ANALYST_PW" "SELECT count() FROM sandbox._rbac_probe"
assert_denied  "analyst CANNOT SELECT from raw"    analyst "$ANALYST_PW" "SELECT count() FROM raw._rbac_probe"

# ----------------------------------------------------------------------------
# bi_dashboard — same read grants as analyst; different profile/quota
# ----------------------------------------------------------------------------
echo "── bi_dashboard ──"
assert_ok      "bi_dashboard can connect"          bi_dashboard "$BI_PW" "SELECT 1"
assert_ok      "bi_dashboard can SELECT main"      bi_dashboard "$BI_PW" "SELECT count() FROM main._rbac_probe"
assert_denied  "bi_dashboard CANNOT INSERT main"   bi_dashboard "$BI_PW" "INSERT INTO main._rbac_probe VALUES (3)"
assert_denied  "bi_dashboard CANNOT SELECT raw"    bi_dashboard "$BI_PW" "SELECT count() FROM raw._rbac_probe"

# ----------------------------------------------------------------------------
# data_scientist — read main.*, read/write sandbox.*
# ----------------------------------------------------------------------------
echo "── data_scientist ──"
assert_ok      "data_scientist can connect"                  data_scientist "$DS_PW" "SELECT 1"
assert_ok      "data_scientist can SELECT main"              data_scientist "$DS_PW" "SELECT count() FROM main._rbac_probe"
assert_ok      "data_scientist can INSERT sandbox"           data_scientist "$DS_PW" "INSERT INTO sandbox._rbac_probe VALUES (42)"
assert_ok      "data_scientist can CREATE sandbox.tbl"       data_scientist "$DS_PW" "CREATE TABLE IF NOT EXISTS sandbox._rbac_created (v UInt32) ENGINE = MergeTree ORDER BY v"
assert_ok      "data_scientist can DROP sandbox.tbl"         data_scientist "$DS_PW" "DROP TABLE IF EXISTS sandbox._rbac_created"
assert_denied  "data_scientist CANNOT INSERT main"           data_scientist "$DS_PW" "INSERT INTO main._rbac_probe VALUES (99)"
assert_denied  "data_scientist CANNOT CREATE main.tbl"       data_scientist "$DS_PW" "CREATE TABLE main._rbac_ds_illegal (v UInt32) ENGINE = MergeTree ORDER BY v"
assert_denied  "data_scientist CANNOT SELECT raw"            data_scientist "$DS_PW" "SELECT count() FROM raw._rbac_probe"

# ----------------------------------------------------------------------------
# service_writer — INSERT on raw.* + full access to main.* (dbt runs here)
# ----------------------------------------------------------------------------
echo "── service_writer ──"
assert_ok      "service_writer can connect"                  service_writer "$SW_PW" "SELECT 1"
assert_ok      "service_writer can INSERT raw"               service_writer "$SW_PW" "INSERT INTO raw._rbac_probe VALUES (7)"
assert_ok      "service_writer can SELECT raw"               service_writer "$SW_PW" "SELECT count() FROM raw._rbac_probe"
assert_ok      "service_writer can INSERT main"              service_writer "$SW_PW" "INSERT INTO main._rbac_probe VALUES (8)"
assert_ok      "service_writer can CREATE main.tbl"          service_writer "$SW_PW" "CREATE TABLE IF NOT EXISTS main._rbac_sw_created (v UInt32) ENGINE = MergeTree ORDER BY v"
assert_ok      "service_writer can DROP main.tbl"            service_writer "$SW_PW" "DROP TABLE IF EXISTS main._rbac_sw_created"
assert_denied  "service_writer CANNOT INSERT sandbox"        service_writer "$SW_PW" "INSERT INTO sandbox._rbac_probe VALUES (11)"

# ----------------------------------------------------------------------------
# Cleanup
# ----------------------------------------------------------------------------
run_as default "$ADMIN_PW" "
    DROP TABLE IF EXISTS raw._rbac_probe;
    DROP TABLE IF EXISTS main._rbac_probe;
    DROP TABLE IF EXISTS sandbox._rbac_probe;
" > /dev/null

echo ""
echo "──────────────────────────────────────────"
echo "  Passed: $pass"
echo "  Failed: $fail"
echo "──────────────────────────────────────────"

if [[ $fail -gt 0 ]]; then
    exit 1
fi
