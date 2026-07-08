"""
build_training_dataset.py
--------------------------
Builds a labelled training dataset for fraud detection.

Reads directly from the dbt-produced main.fct_training_dataset table in
ClickHouse, which already joins all offline features, online (cold-start)
features, request-time columns, and fraud labels at the transaction grain
with point-in-time correctness.

Output: training/datasets/training_dataset.parquet

Usage:
    python training/build_training_dataset.py
    python training/build_training_dataset.py --sample-frac 0.5
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path(__file__).parent / "datasets"
OUTPUT_PATH = OUTPUT_DIR / "training_dataset.parquet"


def get_ch_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username="default",
        password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", "admin_pass"),
    )


def main(sample_frac: float) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Reading main.fct_training_dataset from ClickHouse...")
    ch = get_ch_client()

    # Deterministic hash-based sampling. CH's SAMPLE clause requires a
    # SAMPLE BY key in the MergeTree engine (we don't set one), so we use
    # cityHash64(transaction_id) < cutoff instead. Same sample_frac + same
    # transaction_id → same inclusion decision, so re-runs are reproducible.
    if sample_frac < 1.0:
        cutoff = int(sample_frac * (2**64 - 1))
        where_clause = f"WHERE cityHash64(transaction_id) < {cutoff}"
    else:
        where_clause = ""

    query = f"SELECT * FROM main.fct_training_dataset {where_clause}"
    df = ch.query_df(query)
    ch.close()

    total = len(df)
    fraud_count = int(df["is_fraud"].sum())
    print(f"  {total:,} rows  fraud={fraud_count:,} ({fraud_count/total*100:.2f}%)")

    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nSaved → {OUTPUT_PATH}  ({total:,} rows, {len(df.columns)} columns)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build fraud detection training dataset from ClickHouse")
    parser.add_argument(
        "--sample-frac",
        type=float,
        default=1.0,
        help="Fraction of rows to sample deterministically via cityHash64(transaction_id) (default: 1.0 = all)",
    )
    args = parser.parse_args()
    main(args.sample_frac)
