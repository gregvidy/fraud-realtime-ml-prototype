"""
build_training_dataset.py
--------------------------
Builds a labelled training dataset for fraud detection.

Reads directly from the dbt-produced fct_training_dataset table in DuckDB,
which already joins all offline features, online (cold-start) features,
request-time columns, and fraud labels at the transaction grain with
point-in-time correctness.

Output: training/datasets/training_dataset.parquet

Usage:
    python training/build_training_dataset.py
    python training/build_training_dataset.py --sample-frac 0.5
    python training/build_training_dataset.py --db-path path/to/fraud_offline.duckdb
"""

import argparse
from pathlib import Path

import duckdb

_ROOT = Path(__file__).parents[1]
DEFAULT_DB_PATH = _ROOT / "data" / "duckdb" / "fraud_offline.duckdb"

OUTPUT_DIR = Path(__file__).parent / "datasets"
OUTPUT_PATH = OUTPUT_DIR / "training_dataset.parquet"


def main(sample_frac: float, db_path: Path) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Reading fct_training_dataset from DuckDB...")
    conn = duckdb.connect(str(db_path), read_only=True)

    # Optional sampling via TABLESAMPLE for large datasets
    sample_clause = ""
    if sample_frac < 1.0:
        pct = sample_frac * 100
        sample_clause = f"USING SAMPLE {pct:.1f} PERCENT (bernoulli, 42)"

    query = f"SELECT * FROM main.fct_training_dataset {sample_clause}"
    df = conn.execute(query).df()
    conn.close()

    total = len(df)
    fraud_count = int(df["is_fraud"].sum())
    print(f"  {total:,} rows  fraud={fraud_count:,} ({fraud_count/total*100:.2f}%)")

    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nSaved → {OUTPUT_PATH}  ({total:,} rows, {len(df.columns)} columns)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build fraud detection training dataset")
    parser.add_argument("--sample-frac", type=float, default=1.0,
                        help="Fraction of rows to sample (default: 1.0 = all)")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH,
                        help=f"DuckDB path (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args()
    main(args.sample_frac, args.db_path)
