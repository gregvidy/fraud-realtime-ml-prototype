"""
export_pg_to_duckdb.py
----------------------
Exports all raw operational tables from PostgreSQL into the local DuckDB
offline store (data/duckdb/fraud_offline.duckdb).

Each table is loaded into a `raw` schema inside DuckDB, matching the schema
name referenced in dbt sources.yml.  Run this before `dbt run --target duckdb`.

Usage:
    python scripts/export_pg_to_duckdb.py [--db-path data/duckdb/fraud_offline.duckdb]
"""

import argparse
import os
from pathlib import Path

import duckdb
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Tables to export from PostgreSQL → DuckDB raw schema
# ---------------------------------------------------------------------------
RAW_TABLES = [
    "raw_transactions",
    "raw_users",
    "raw_devices",
    "raw_merchants",
    "raw_login_events",
    "fraud_labels",
]

DEFAULT_DB_PATH = Path(__file__).parents[1] / "data" / "duckdb" / "fraud_offline.duckdb"


def get_pg_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


def export_table(pg_conn, duck_conn: duckdb.DuckDBPyConnection, table: str) -> None:
    print(f"  Exporting {table}...", end="", flush=True)
    df = pd.read_sql(f"SELECT * FROM {table}", pg_conn)  # noqa: S608  (internal tool, fixed table names)
    duck_conn.execute(f"DROP TABLE IF EXISTS raw.{table}")
    duck_conn.execute(f"CREATE TABLE raw.{table} AS SELECT * FROM df")
    row_count = duck_conn.execute(f"SELECT COUNT(*) FROM raw.{table}").fetchone()[0]
    print(f" {row_count:,} rows")


def main(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"DuckDB target: {db_path}")
    duck_conn = duckdb.connect(str(db_path))

    # Ensure the raw schema exists
    duck_conn.execute("CREATE SCHEMA IF NOT EXISTS raw")

    print("Connecting to PostgreSQL...")
    pg_conn = get_pg_conn()

    print("Exporting tables to DuckDB raw schema:")
    for table in RAW_TABLES:
        export_table(pg_conn, duck_conn, table)

    pg_conn.close()
    duck_conn.close()
    print("Export complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Postgres raw tables to DuckDB")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to DuckDB file (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    main(args.db_path)
