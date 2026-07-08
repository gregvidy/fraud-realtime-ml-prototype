"""
export_pg_to_clickhouse.py
--------------------------
Copies raw operational tables from PostgreSQL into the ClickHouse `raw` schema.

Uses ClickHouse's built-in `postgresql()` table function so the data hop is
server-side (no pandas). Applied idempotently: DDL from raw_schema.sql is run
first (CREATE TABLE IF NOT EXISTS), then each table is TRUNCATE'd and
re-populated with INSERT ... SELECT ... FROM postgresql(...).

Row counts are compared to Postgres after each table and mismatches raise.

Usage:
    python scripts/export_pg_to_clickhouse.py
    python scripts/export_pg_to_clickhouse.py --tables raw_users,raw_transactions
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import clickhouse_connect
import psycopg2
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL_PATH = ROOT / "infra" / "clickhouse" / "raw_schema.sql"


# ---------------------------------------------------------------------------
# Table export specs
# ---------------------------------------------------------------------------
# Each spec lists the columns in Postgres order and the column expression to
# use when SELECTing through the postgresql() table function. Most columns
# pass through unchanged; INET columns need explicit ::TEXT casts because the
# postgresql() function cannot map INET to any ClickHouse type.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]      # ClickHouse column names (order matches raw_schema.sql)
    pg_select: str                # SELECT list issued against the PG side

    @property
    def column_list(self) -> str:
        return ", ".join(self.columns)


TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="raw_users",
        columns=(
            "user_id", "email", "phone", "country_code", "signup_date",
            "account_type", "is_verified", "event_timestamp",
            "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "user_id, email, phone, country_code, signup_date, "
            "account_type, is_verified, event_timestamp, "
            "ingestion_timestamp, created_at"
        ),
    ),
    TableSpec(
        name="raw_devices",
        columns=(
            "device_event_id", "device_id", "user_id", "device_fingerprint",
            "platform", "os_version", "ip_address", "country_code",
            "event_timestamp", "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "device_event_id, device_id, user_id, device_fingerprint, "
            "platform, os_version, ip_address::TEXT AS ip_address, "
            "country_code, event_timestamp, ingestion_timestamp, created_at"
        ),
    ),
    TableSpec(
        name="raw_merchants",
        columns=(
            "merchant_id", "merchant_name", "merchant_category", "country_code",
            "is_online", "risk_tier", "event_timestamp",
            "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "merchant_id, merchant_name, merchant_category, country_code, "
            "is_online, risk_tier, event_timestamp, "
            "ingestion_timestamp, created_at"
        ),
    ),
    TableSpec(
        name="raw_transactions",
        columns=(
            "transaction_id", "user_id", "device_id", "merchant_id", "amount",
            "currency", "payment_method", "country_code", "ip_address",
            "is_international", "txn_status", "decline_reason", "local_hour",
            "event_timestamp", "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "transaction_id, user_id, device_id, merchant_id, amount, "
            "currency, payment_method, country_code, "
            "ip_address::TEXT AS ip_address, is_international, "
            "txn_status, decline_reason, local_hour, event_timestamp, "
            "ingestion_timestamp, created_at"
        ),
    ),
    TableSpec(
        name="raw_login_events",
        columns=(
            "login_event_id", "user_id", "device_id", "ip_address",
            "country_code", "login_status", "failure_reason",
            "event_timestamp", "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "login_event_id, user_id, device_id, "
            "ip_address::TEXT AS ip_address, country_code, "
            "login_status, failure_reason, event_timestamp, "
            "ingestion_timestamp, created_at"
        ),
    ),
    TableSpec(
        name="fraud_labels",
        columns=(
            "transaction_id", "is_fraud", "fraud_type", "label_source",
            "label_timestamp", "ingestion_timestamp", "created_at",
        ),
        pg_select=(
            "transaction_id, is_fraud, fraud_type, label_source, "
            "label_timestamp, ingestion_timestamp, created_at"
        ),
    ),
)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


def get_ch_client():
    # Admin credentials: this is a one-shot ETL utility that manages DDL and
    # TRUNCATE for the raw schema. Long-running services (streaming consumers,
    # dbt runs) use `service_writer` with narrower grants.
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username="default",
        password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", "admin_pass"),
    )


# `postgresql()` runs INSIDE ClickHouse; the host/port here is how the
# ClickHouse container reaches Postgres. Defaults to the compose service name.
PG_HOST_FROM_CH = os.getenv("POSTGRES_HOST_FROM_CH", "postgres")
PG_PORT_FROM_CH = os.getenv("POSTGRES_PORT_FROM_CH", "5432")


# ---------------------------------------------------------------------------
# Export logic
# ---------------------------------------------------------------------------

def apply_schema(ch) -> None:
    print(f"Applying schema from {SCHEMA_SQL_PATH.name}...")
    sql = SCHEMA_SQL_PATH.read_text()
    # Strip line comments before splitting on `;` — otherwise a `;` inside a
    # comment (e.g. "postgresql() table function; cast in ...") splits mid-comment
    # and yields a chunk that isn't valid SQL.
    stripped = "\n".join(
        (line[: line.index("--")] if "--" in line else line)
        for line in sql.splitlines()
    )
    for stmt in (s.strip() for s in stripped.split(";")):
        if stmt:
            ch.command(stmt)


def export_table(ch, pg_conn, spec: TableSpec) -> tuple[int, int]:
    """Export one table; return (pg_row_count, ch_row_count)."""
    pg_user = os.getenv("POSTGRES_USER", "fraud_user")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "fraud_pass")
    pg_db = os.getenv("POSTGRES_DB", "fraud_db")
    pg_schema = os.getenv("POSTGRES_SCHEMA", "public")

    # Truncate the target so re-runs are idempotent.
    ch.command(f"TRUNCATE TABLE raw.{spec.name}")

    # Server-side copy. postgresql() signature:
    #   postgresql('host:port', 'db', 'table', 'user', 'password'[, 'schema'])
    # We wrap it in a subquery so we can control the projection (INET casts).
    #
    # Note: we're relying on the postgresql() function's ability to run
    # arbitrary SQL via the `query` parameter (CH 24+). For POC we use a
    # simpler pattern: fetch the whole table with column projection outside.
    insert_sql = f"""
        INSERT INTO raw.{spec.name} ({spec.column_list})
        SELECT {spec.pg_select}
        FROM postgresql(
            '{PG_HOST_FROM_CH}:{PG_PORT_FROM_CH}',
            '{pg_db}',
            '{spec.name}',
            '{pg_user}',
            '{pg_pass}',
            '{pg_schema}'
        )
    """
    ch.command(insert_sql)

    ch_count = int(ch.query(f"SELECT count() FROM raw.{spec.name}").result_rows[0][0])
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {spec.name}")  # noqa: S608 — fixed table names
        pg_count = int(cur.fetchone()[0])
    return pg_count, ch_count


def main(selected: list[str] | None) -> int:
    ch = get_ch_client()
    apply_schema(ch)

    with get_pg_conn() as pg_conn:
        print(f"Exporting Postgres → ClickHouse (raw schema) via "
              f"postgresql('{PG_HOST_FROM_CH}:{PG_PORT_FROM_CH}', ...)")

        tables = [s for s in TABLES if selected is None or s.name in selected]
        failures: list[tuple[str, int, int]] = []

        for spec in tables:
            print(f"  Exporting {spec.name} ... ", end="", flush=True)
            pg_count, ch_count = export_table(ch, pg_conn, spec)
            status = "OK" if pg_count == ch_count else "MISMATCH"
            print(f"pg={pg_count:>10,}  ch={ch_count:>10,}  [{status}]")
            if pg_count != ch_count:
                failures.append((spec.name, pg_count, ch_count))

        if failures:
            print("\nRow-count mismatches:")
            for name, pg_c, ch_c in failures:
                print(f"  {name}: pg={pg_c} vs ch={ch_c}")
            return 1

    print("Export complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Postgres raw tables to ClickHouse")
    parser.add_argument(
        "--tables",
        type=str,
        default=None,
        help="Comma-separated subset of tables to export (default: all)",
    )
    args = parser.parse_args()
    selected = args.tables.split(",") if args.tables else None
    sys.exit(main(selected))
