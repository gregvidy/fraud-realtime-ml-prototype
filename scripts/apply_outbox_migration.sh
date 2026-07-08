#!/usr/bin/env bash
# ============================================================================
# Apply the outbox migration to a running fraud_postgres container.
# Idempotent — safe to re-run (CREATE TABLE IF NOT EXISTS + partial index).
# ============================================================================
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-fraud_postgres}"
PG_USER="${POSTGRES_USER:-fraud_user}"
PG_DB="${POSTGRES_DB:-fraud_db}"
PG_PASS="${POSTGRES_PASSWORD:-fraud_pass}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="${SCRIPT_DIR}/../sql/bootstrap/02_outbox.sql"

if [ ! -f "$SQL_FILE" ]; then
    echo "ERROR: SQL file not found: $SQL_FILE" >&2
    exit 1
fi

echo "── Applying $SQL_FILE to $PG_CONTAINER ──"
docker exec -i -e PGPASSWORD="$PG_PASS" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -f - < "$SQL_FILE"

echo ""
echo "── outbox_events schema ──"
docker exec -e PGPASSWORD="$PG_PASS" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d "$PG_DB" -c "\d outbox_events"
