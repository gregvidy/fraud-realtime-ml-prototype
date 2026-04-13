"""
build_training_dataset.py
--------------------------
Pulls the fct_training_dataset view from Postgres (built by dbt) and
saves it as a Parquet file ready for model training.

Note: we read directly from the dbt output table rather than Feast
historical features because all feature computation already happened
in dbt. This is the simplest and most reproducible path for an MVP.

Output: training/datasets/training_dataset.parquet
"""

import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path(__file__).parent / "datasets"
OUTPUT_PATH = OUTPUT_DIR / "training_dataset.parquet"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


QUERY = """
SELECT
    transaction_id,
    user_id,
    device_id,
    merchant_id,
    event_timestamp,

    -- request-time
    txn_amount,
    is_international,
    local_hour,

    -- user offline
    user_account_age_days,
    user_is_verified,
    user_is_standard_account,
    user_txn_count_1d,
    user_txn_count_7d,
    user_txn_count_30d,
    user_txn_amount_sum_1d,
    user_txn_amount_sum_7d,
    user_txn_amount_sum_30d,
    user_avg_ticket_30d,
    user_distinct_merchants_30d,
    user_distinct_devices_30d,
    user_decline_count_7d,
    user_failed_logins_7d,
    user_failed_logins_1d,

    -- device offline
    device_distinct_users_30d,
    device_txn_count_7d,
    device_txn_count_1d,
    device_is_shared_flag,

    -- merchant offline
    merchant_is_high_risk,
    merchant_is_online,
    merchant_txn_count_30d,
    merchant_avg_ticket_30d,
    merchant_fraud_rate_30d,

    -- user online
    user_txn_count_5m,
    user_txn_count_10m,
    user_txn_count_1h,
    user_txn_amount_sum_5m,
    user_txn_amount_sum_10m,
    user_txn_amount_sum_1h,
    user_distinct_merchants_5m,
    user_distinct_merchants_10m,
    user_distinct_merchants_1h,
    user_failed_logins_15m,
    user_failed_logins_1h,

    -- device online
    device_txn_count_5m,
    device_txn_count_10m,
    device_txn_count_1h,

    -- label
    is_fraud

FROM fct_training_dataset
WHERE txn_amount IS NOT NULL
  AND is_fraud IS NOT NULL
ORDER BY event_timestamp
"""


def main(sample_frac: float = 1.0) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Connecting to Postgres...")
    conn = get_conn()

    print("Pulling training dataset from fct_training_dataset...")
    df = pd.read_sql(QUERY, conn)
    conn.close()

    if sample_frac < 1.0:
        df = df.sample(frac=sample_frac, random_state=42)

    fraud_count = df["is_fraud"].sum()
    total       = len(df)
    print(f"Dataset: {total:,} rows  fraud={fraud_count:,} ({fraud_count/total*100:.2f}%)")

    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-frac", type=float, default=1.0,
                        help="Fraction of rows to sample (default 1.0 = all)")
    args = parser.parse_args()
    main(args.sample_frac)
