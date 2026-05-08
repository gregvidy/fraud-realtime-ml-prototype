"""
materialize_features.py
-----------------------
Full offline feature pipeline:
  1. Export DuckDB feature tables to Parquet (Feast-compatible).
  2. Materialize Parquet features into Redis via Feast.

Run this after `dbt run` completes against DuckDB.

Usage:
    python scripts/materialize_features.py [--days 2] [--db-path data/duckdb/fraud_offline.duckdb]
"""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from feast import FeatureStore

load_dotenv()

ROOT = Path(__file__).parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "duckdb" / "fraud_offline.duckdb"
PARQUET_DIR = ROOT / "data" / "duckdb" / "parquet"
FEAST_REPO = ROOT / "feast_repo" / "feature_repo"

# Feature table → parquet filename mapping.  Version suffix _v1 aligns with
# Feast feature view names (user_batch_fv_v1, device_batch_fv_v1, …).
# fct_training_dataset is excluded — training datasets are now built via
# Feast get_historical_features() + online_feature_log.
FEATURE_EXPORTS: dict[str, str] = {
    "main.fct_user_features":     "fct_user_features_v1.parquet",
    "main.fct_device_features":   "fct_device_features_v1.parquet",
    "main.fct_merchant_features": "fct_merchant_features_v1.parquet",
}


def export_parquet(db_path: Path) -> None:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Connecting to DuckDB: {db_path}")
    conn = duckdb.connect(str(db_path), read_only=True)

    for table, filename in FEATURE_EXPORTS.items():
        out = PARQUET_DIR / filename

        # Build a SELECT that casts DECIMAL columns to DOUBLE so Feast can
        # serialize them (Feast proto doesn't support decimal.Decimal).
        schema_rows = conn.execute(f"DESCRIBE {table}").fetchall()
        col_exprs = []
        for col_name, col_type, *_ in schema_rows:
            if col_type.upper().startswith("DECIMAL") or col_type.upper().startswith("NUMERIC"):
                col_exprs.append(f'CAST("{col_name}" AS DOUBLE) AS "{col_name}"')
            else:
                col_exprs.append(f'"{col_name}"')
        select_clause = ", ".join(col_exprs)

        print(f"  Exporting {table} → {out.name} ...", end="", flush=True)
        conn.execute(
            f"COPY (SELECT {select_clause} FROM {table}) TO '{out}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE TRUE)"
        )
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f" {row_count:,} rows")

    conn.close()
    print("Parquet export complete.")


def _detect_parquet_range() -> tuple[datetime, datetime]:
    """Detect min/max event_timestamp across the three feature parquet files."""
    import pyarrow.parquet as pq

    files = [
        PARQUET_DIR / "fct_user_features_v1.parquet",
        PARQUET_DIR / "fct_device_features_v1.parquet",
        PARQUET_DIR / "fct_merchant_features_v1.parquet",
    ]
    ts_min, ts_max = None, None
    for f in files:
        tbl = pq.read_table(f, columns=["event_timestamp"])
        col = tbl.column("event_timestamp")
        lo = col.to_pylist()
        lo = [v for v in lo if v is not None]
        if not lo:
            continue
        lo_min = min(lo)
        lo_max = max(lo)
        if ts_min is None or lo_min < ts_min:
            ts_min = lo_min
        if ts_max is None or lo_max > ts_max:
            ts_max = lo_max

    # Ensure UTC-aware datetimes
    def _utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return _utc(ts_min), _utc(ts_max)


def materialize(days: int) -> None:
    print(f"Loading Feast store from {FEAST_REPO}")
    store = FeatureStore(repo_path=str(FEAST_REPO))

    if days == 0:
        # Auto-detect range from parquet data
        start_dt, end_dt = _detect_parquet_range()
        # Feast requires end_date <= now; cap at now + 1 s to avoid edge issues
        now = datetime.now(timezone.utc)
        if end_dt > now:
            end_dt = now
    else:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)

    print(f"Materializing features  {start_dt.date()} → {end_dt.date()}")
    store.materialize(start_date=start_dt, end_date=end_dt)
    print("Materialization complete.")


def main(db_path: Path, days: int, skip_export: bool = False) -> None:
    if skip_export:
        print("Skipping DuckDB export (using existing parquet files)")
    else:
        export_parquet(db_path)
    materialize(days)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export DuckDB features to Parquet and materialize into Redis")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Number of days back to materialize (default: 0 = auto-detect from parquet data range)",
    )
    parser.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip DuckDB→Parquet export (use when parquet files already exist from S3)",
    )
    args = parser.parse_args()
    main(args.db_path, args.days, skip_export=args.skip_export)
