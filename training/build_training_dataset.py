"""
build_training_dataset.py
--------------------------
Builds a labelled training dataset for fraud detection.

Reads directly from the dbt-produced main.fct_training_dataset table in
ClickHouse, which already joins all offline features, online (cold-start)
features, request-time columns, and fraud labels at the transaction grain
with point-in-time correctness.

Output: training/datasets/training_dataset.parquet

B6: when running as a KFP component in-cluster, ClickHouse is unreachable
and the parquet is expected to have been pre-staged in MinIO by
`make bootstrap-data`. Setting the ``TRAINING_DATA_URI`` env var to an
``s3://...`` path switches the script into "verify only" mode — no CH
query, just an existence check. This is the mirror of the READ path
that train_model.py already honours (B5b).

Usage:
    python training/build_training_dataset.py
    python training/build_training_dataset.py --sample-frac 0.5
    TRAINING_DATA_URI=s3://fraudml-data/training/datasets/training_dataset.parquet \\
        python training/build_training_dataset.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path(__file__).parent / "datasets"
OUTPUT_PATH = OUTPUT_DIR / "training_dataset.parquet"


def _verify_s3_existence(uri: str) -> int:
    """KFP mode: assert the pre-staged parquet exists at ``uri``.

    Returns the process exit code (0 on success, 1 on missing object).
    """
    import s3fs

    fs = s3fs.S3FileSystem()  # honours AWS_ENDPOINT_URL_S3 + AWS_* env vars
    key = uri.replace("s3://", "", 1)
    if fs.exists(key):
        info = fs.info(key)
        size_mib = info.get("size", 0) / (1024 * 1024)
        print(f"[build_dataset] pre-staged training data present at {uri} ({size_mib:.1f} MiB) — skipping ClickHouse rebuild.")
        return 0
    print(
        f"[build_dataset] ERROR: TRAINING_DATA_URI={uri} not found in MinIO. "
        "Run `make bootstrap-data` first to stage local parquets.",
        file=sys.stderr,
    )
    return 1


def get_ch_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username="default",
        password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", "admin_pass"),
    )


def main(sample_frac: float) -> int:
    uri = os.getenv("TRAINING_DATA_URI")
    if uri and "://" in uri:
        return _verify_s3_existence(uri)

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
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build fraud detection training dataset from ClickHouse")
    parser.add_argument(
        "--sample-frac",
        type=float,
        default=1.0,
        help="Fraction of rows to sample deterministically via cityHash64(transaction_id) (default: 1.0 = all)",
    )
    args = parser.parse_args()
    raise SystemExit(main(args.sample_frac))
