#!/usr/bin/env bash
# ============================================================================
# Apply / re-apply Slice 9 streaming DDL to a running ClickHouse container.
#
# Runs `infra/clickhouse/streaming.sql` as the `default` admin user. Kafka
# Engine tables and MVs are dropped and recreated (idempotent, no data loss
# because destination MergeTree tables use CREATE IF NOT EXISTS). Consumer
# offsets survive drops because they live in the Redpanda __consumer_offsets
# topic — to fully rewind, bump the `*-v1` group name suffix in the SQL.
#
# Usage:
#   scripts/apply_clickhouse_streaming.sh
# ============================================================================
set -euo pipefail

CH_CONTAINER="${CH_CONTAINER:-fraud_clickhouse}"
CH_USER="${CH_USER:-default}"
CH_PASS="${CH_PASS:-${CLICKHOUSE_ADMIN_PASSWORD:-admin_pass}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="${SCRIPT_DIR}/../infra/clickhouse/streaming.sql"

if [ ! -f "$SQL_FILE" ]; then
    echo "ERROR: SQL file not found: $SQL_FILE" >&2
    exit 1
fi

echo "── Applying $SQL_FILE to $CH_CONTAINER ──"
docker exec -i "$CH_CONTAINER" clickhouse-client \
    --user "$CH_USER" \
    --password "$CH_PASS" \
    --multiquery < "$SQL_FILE"

echo ""
echo "── Streaming DDL applied. Objects: ──"
docker exec "$CH_CONTAINER" clickhouse-client \
    --user "$CH_USER" \
    --password "$CH_PASS" \
    --query "SELECT database, name, engine FROM system.tables
             WHERE database IN ('raw','main') AND name LIKE 'stream_%' OR name LIKE 'mv_stream_%'
             ORDER BY database, name FORMAT PrettyCompactMonoBlock"
