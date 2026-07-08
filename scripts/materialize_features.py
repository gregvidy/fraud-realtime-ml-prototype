"""
materialize_features.py
-----------------------
Full offline feature pipeline:
  1. Export ClickHouse feature tables to Parquet (Feast-compatible).
  2. Materialize Parquet features into Redis via Feast.

Run this after `dbt run --target clickhouse` completes.

Usage:
    python scripts/materialize_features.py [--days 0]
    python scripts/materialize_features.py --skip-export      # reuse existing parquet
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clickhouse_connect
from dotenv import load_dotenv
from feast import FeatureStore

load_dotenv()

ROOT = Path(__file__).parents[1]
PARQUET_DIR = ROOT / "data" / "parquet"
FEAST_REPO = ROOT / "feast_repo" / "feature_repo"

# Feature table → parquet filename mapping. The _v1 suffix aligns with
# Feast feature view names (user_batch_fv_v1, device_batch_fv_v1, …).
FEATURE_EXPORTS: dict[str, str] = {
    "main.fct_user_features":     "fct_user_features_v1.parquet",
    "main.fct_device_features":   "fct_device_features_v1.parquet",
    "main.fct_merchant_features": "fct_merchant_features_v1.parquet",
}


def get_ch_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username="default",
        password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", "admin_pass"),
    )


def export_parquet() -> None:
    """Stream CH feature tables to Parquet via Arrow."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    ch = get_ch_client()

    for table, filename in FEATURE_EXPORTS.items():
        out = PARQUET_DIR / filename
        print(f"  Exporting {table} → {out.name} ...", end="", flush=True)

        arrow_table: pa.Table = ch.query_arrow(f"SELECT * FROM {table}")

        # Cast any Decimal columns to Float64 — Feast's protobuf layer
        # doesn't accept decimal.Decimal at materialization time.
        new_fields = []
        needs_cast = False
        for field in arrow_table.schema:
            if pa.types.is_decimal(field.type):
                new_fields.append(pa.field(field.name, pa.float64()))
                needs_cast = True
            else:
                new_fields.append(field)
        if needs_cast:
            arrow_table = arrow_table.cast(pa.schema(new_fields))

        pq.write_table(arrow_table, str(out), compression="snappy")
        print(f" {arrow_table.num_rows:,} rows")

    ch.close()
    print("Parquet export complete.")


def _detect_parquet_range() -> tuple[datetime, datetime]:
    """Detect min/max event_timestamp across the three feature parquet files."""
    import pyarrow.parquet as pq

    files = [PARQUET_DIR / fn for fn in FEATURE_EXPORTS.values()]
    ts_min, ts_max = None, None
    for f in files:
        tbl = pq.read_table(f, columns=["event_timestamp"])
        col = [v for v in tbl.column("event_timestamp").to_pylist() if v is not None]
        if not col:
            continue
        lo_min, lo_max = min(col), max(col)
        if ts_min is None or lo_min < ts_min:
            ts_min = lo_min
        if ts_max is None or lo_max > ts_max:
            ts_max = lo_max

    def _utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return _utc(ts_min), _utc(ts_max)


def materialize(days: int) -> None:
    print(f"Loading Feast store from {FEAST_REPO}")
    store = FeatureStore(repo_path=str(FEAST_REPO))

    if days == 0:
        start_dt, end_dt = _detect_parquet_range()
        now = datetime.now(timezone.utc)
        if end_dt > now:
            end_dt = now
    else:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)

    print(f"Materializing features  {start_dt.date()} → {end_dt.date()}")
    store.materialize(start_date=start_dt, end_date=end_dt)
    print("Materialization complete.")


def main(days: int, skip_export: bool = False) -> None:
    if skip_export:
        print("Skipping ClickHouse export (using existing parquet files)")
    else:
        print(f"Exporting ClickHouse main.fct_*_features → {PARQUET_DIR}")
        export_parquet()
    materialize(days)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export ClickHouse feature tables to Parquet and materialize into Redis via Feast"
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
        help="Skip ClickHouse→Parquet export (use when parquet files already exist)",
    )
    args = parser.parse_args()
    main(args.days, skip_export=args.skip_export)
