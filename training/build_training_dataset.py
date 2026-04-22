"""
build_training_dataset.py
--------------------------
Builds a labelled training dataset for fraud detection.

Data flow:
  1. Entity + label query  — raw_transactions JOIN fraud_labels (Postgres)
  2. Offline features      — Feast get_historical_features() PIT join over
                             dbt-produced fct_*_features parquet files
  3. Online features       — online_feature_log (Postgres) — actual values
                             served at inference time by retriever.py.
                             Falls back to dbt-computed cold-start values
                             (from int_*_online_stats in DuckDB) when the
                             log has no entry for a transaction.

Output: training/datasets/training_dataset.parquet

Usage:
    python training/build_training_dataset.py
    python training/build_training_dataset.py --sample-frac 0.5
    python training/build_training_dataset.py --feast-repo path/to/feature_repo
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

_ROOT = Path(__file__).parents[1]
DEFAULT_FEAST_REPO = _ROOT / "feast_repo" / "feature_repo"
DEFAULT_DB_PATH    = _ROOT / "data" / "duckdb" / "fraud_offline.duckdb"

# Online feature columns captured by feature_logger.py at inference time.
ONLINE_FEATURE_COLS: list[str] = [
    "user_txn_count_5m",
    "user_txn_count_10m",
    "user_txn_count_1h",
    "user_txn_amount_sum_5m",
    "user_txn_amount_sum_10m",
    "user_txn_amount_sum_1h",
    "user_distinct_merchants_5m",
    "user_distinct_merchants_10m",
    "user_distinct_merchants_1h",
    "user_failed_logins_15m",
    "user_failed_logins_1h",
    "device_txn_count_5m",
    "device_txn_count_10m",
    "device_txn_count_1h",
]

# Offline features to retrieve via Feast PIT join.
FEAST_FEATURES: list[str] = [
    "user_batch_fv_v1:user_account_age_days",
    "user_batch_fv_v1:user_is_verified",
    "user_batch_fv_v1:user_is_standard_account",
    "user_batch_fv_v1:user_txn_count_1d",
    "user_batch_fv_v1:user_txn_count_7d",
    "user_batch_fv_v1:user_txn_count_30d",
    "user_batch_fv_v1:user_txn_amount_sum_1d",
    "user_batch_fv_v1:user_txn_amount_sum_7d",
    "user_batch_fv_v1:user_txn_amount_sum_30d",
    "user_batch_fv_v1:user_avg_ticket_30d",
    "user_batch_fv_v1:user_distinct_merchants_30d",
    "user_batch_fv_v1:user_distinct_devices_30d",
    "user_batch_fv_v1:user_decline_count_7d",
    "user_batch_fv_v1:user_failed_logins_7d",
    "user_batch_fv_v1:user_failed_logins_1d",
    "device_batch_fv_v1:device_distinct_users_30d",
    "device_batch_fv_v1:device_txn_count_7d",
    "device_batch_fv_v1:device_txn_count_1d",
    "device_batch_fv_v1:device_is_shared_flag",
    "merchant_batch_fv_v1:merchant_is_high_risk",
    "merchant_batch_fv_v1:merchant_is_online",
    "merchant_batch_fv_v1:merchant_txn_count_30d",
    "merchant_batch_fv_v1:merchant_avg_ticket_30d",
    "merchant_batch_fv_v1:merchant_fraud_rate_30d",
]


# ---------------------------------------------------------------------------
# Step 1 — entity + label DataFrame from Postgres
# ---------------------------------------------------------------------------

def _fetch_entity_df() -> pd.DataFrame:
    """
    Pull labelled transaction entities from Postgres.
    Returns a DataFrame with columns:
        transaction_id, user_id, device_id, merchant_id,
        event_timestamp, txn_amount, is_international, local_hour, is_fraud
    """
    query = """
        SELECT
            t.transaction_id,
            t.user_id,
            t.device_id,
            t.merchant_id,
            t.event_timestamp,
            t.amount          AS txn_amount,
            t.is_international::int AS is_international,
            t.local_hour,
            l.is_fraud
        FROM raw_transactions t
        JOIN fraud_labels l USING (transaction_id)
        WHERE l.is_fraud IS NOT NULL
          AND t.amount IS NOT NULL
        ORDER BY t.event_timestamp
    """
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )
    df = pd.read_sql(query, conn)
    conn.close()
    # Feast requires timezone-aware event_timestamp
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], utc=True)
    return df


# ---------------------------------------------------------------------------
# Step 2 — offline features via Feast PIT join
# ---------------------------------------------------------------------------

FEAST_CHUNK_SIZE = 50_000  # rows per Feast PIT join batch to limit peak RAM


def _fetch_offline_features(entity_df: pd.DataFrame, feast_repo: Path) -> pd.DataFrame:
    """
    Use Feast get_historical_features() to do a point-in-time correct join
    of offline (batch) features onto the entity DataFrame.

    Processes in chunks of FEAST_CHUNK_SIZE to avoid OOM on large datasets.
    Returns a DataFrame with entity columns + offline feature columns.
    """
    from feast import FeatureStore
    store = FeatureStore(repo_path=str(feast_repo))
    total = len(entity_df)
    print(f"  Running Feast PIT join for {total:,} entities in chunks of {FEAST_CHUNK_SIZE:,}...")

    entity_cols = ["transaction_id", "user_id", "device_id", "merchant_id", "event_timestamp"]
    chunks = []
    for start in range(0, total, FEAST_CHUNK_SIZE):
        end = min(start + FEAST_CHUNK_SIZE, total)
        chunk = entity_df.iloc[start:end][entity_cols]
        print(f"    chunk {start:,}–{end:,} ...", end="\r", flush=True)
        job = store.get_historical_features(entity_df=chunk, features=FEAST_FEATURES)
        chunks.append(job.to_df())
    print(f"  Feast PIT join complete ({total:,} rows)." + " " * 20)
    return pd.concat(chunks, ignore_index=True)


# ---------------------------------------------------------------------------
# Step 3 — online features from online_feature_log (Postgres)
#           with cold-start fallback from DuckDB int_*_online_stats
# ---------------------------------------------------------------------------

def _fetch_online_feature_log() -> pd.DataFrame:
    """
    Pull actual inference-time online features from Postgres online_feature_log.
    Returns a DataFrame indexed by transaction_id.
    """
    cols = ", ".join(ONLINE_FEATURE_COLS)
    query = f"SELECT transaction_id, {cols} FROM online_feature_log"
    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            user=os.getenv("POSTGRES_USER", "fraud_user"),
            password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
            dbname=os.getenv("POSTGRES_DB", "fraud_db"),
        )
        df = pd.read_sql(query, conn)
        conn.close()
        return df.set_index("transaction_id")
    except Exception as exc:
        print(f"  WARNING: could not fetch online_feature_log: {exc}")
        return pd.DataFrame(columns=["transaction_id"] + ONLINE_FEATURE_COLS).set_index("transaction_id")


def _fetch_coldstart_online_features(db_path: Path) -> pd.DataFrame:
    """
    Fallback: pull SQL-approximated online features from DuckDB
    (int_*_online_stats cold-start models). Used only when online_feature_log
    has no coverage — i.e. the very first training run.
    """
    import duckdb
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        cols = ", ".join(["t.transaction_id"] + [f"COALESCE(uto.{c}, ulo.{c}, dto.{c}, 0) AS {c}"
                          if False else f"t.transaction_id" for c in ONLINE_FEATURE_COLS])
        # Simpler direct query — joins the three cold-start intermediate tables
        query = """
            SELECT
                t.transaction_id,
                COALESCE(uto.user_txn_count_5m, 0)              AS user_txn_count_5m,
                COALESCE(uto.user_txn_count_10m, 0)             AS user_txn_count_10m,
                COALESCE(uto.user_txn_count_1h, 0)              AS user_txn_count_1h,
                COALESCE(uto.user_txn_amount_sum_5m, 0)         AS user_txn_amount_sum_5m,
                COALESCE(uto.user_txn_amount_sum_10m, 0)        AS user_txn_amount_sum_10m,
                COALESCE(uto.user_txn_amount_sum_1h, 0)         AS user_txn_amount_sum_1h,
                COALESCE(uto.user_distinct_merchants_5m, 0)     AS user_distinct_merchants_5m,
                COALESCE(uto.user_distinct_merchants_10m, 0)    AS user_distinct_merchants_10m,
                COALESCE(uto.user_distinct_merchants_1h, 0)     AS user_distinct_merchants_1h,
                COALESCE(ulo.user_failed_logins_15m, 0)         AS user_failed_logins_15m,
                COALESCE(ulo.user_failed_logins_1h, 0)          AS user_failed_logins_1h,
                COALESCE(dto.device_txn_count_5m, 0)            AS device_txn_count_5m,
                COALESCE(dto.device_txn_count_10m, 0)           AS device_txn_count_10m,
                COALESCE(dto.device_txn_count_1h, 0)            AS device_txn_count_1h
            FROM main.stg_transactions t
            LEFT JOIN main.int_user_txn_online_stats    uto USING (transaction_id)
            LEFT JOIN main.int_user_login_online_stats  ulo USING (transaction_id)
            LEFT JOIN main.int_device_txn_online_stats  dto USING (transaction_id)
        """
        df = conn.execute(query).df()
        conn.close()
        return df.set_index("transaction_id")
    except Exception as exc:
        print(f"  WARNING: cold-start fallback also failed: {exc}")
        return pd.DataFrame(columns=["transaction_id"] + ONLINE_FEATURE_COLS).set_index("transaction_id")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(sample_frac: float, feast_repo: Path, db_path: Path) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1 — entities + labels
    print("Step 1/3  Fetching entity + label data from Postgres...")
    entity_df = _fetch_entity_df()
    if sample_frac < 1.0:
        entity_df = entity_df.sample(frac=sample_frac, random_state=42).reset_index(drop=True)
    total = len(entity_df)
    fraud_count = entity_df["is_fraud"].sum()
    print(f"  {total:,} rows  fraud={fraud_count:,} ({fraud_count/total*100:.2f}%)")

    # Step 2 — offline features via Feast PIT join
    print("Step 2/3  Running Feast point-in-time join for offline features...")
    offline_df = _fetch_offline_features(entity_df, feast_repo)

    # Step 3 — online features from inference log (or cold-start fallback)
    print("Step 3/3  Fetching online features from online_feature_log...")
    feature_log = _fetch_online_feature_log()

    unmatched = ~entity_df["transaction_id"].isin(feature_log.index)
    unmatched_count = unmatched.sum()
    print(f"  Matched {total - unmatched_count:,}/{total:,} rows from online_feature_log.")

    if unmatched_count > 0:
        print(f"  {unmatched_count:,} rows have no log entry — using cold-start DuckDB approximation.")
        coldstart_df = _fetch_coldstart_online_features(db_path)
        # Merge log + cold-start: log takes priority
        combined_online = feature_log.combine_first(coldstart_df)
    else:
        combined_online = feature_log

    # Merge offline (Feast PIT) + online features
    df = offline_df.merge(
        combined_online.reset_index(),
        on="transaction_id",
        how="left",
    )
    # Fill any remaining nulls in online cols with 0
    df[ONLINE_FEATURE_COLS] = df[ONLINE_FEATURE_COLS].fillna(0)

    # Carry forward request-time columns from entity_df
    request_cols = ["transaction_id", "txn_amount", "is_international", "local_hour", "is_fraud"]
    df = df.merge(entity_df[request_cols], on="transaction_id", how="left")

    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nSaved → {OUTPUT_PATH}  ({len(df):,} rows, {len(df.columns)} features)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build fraud detection training dataset")
    parser.add_argument("--sample-frac", type=float, default=1.0,
                        help="Fraction of rows to sample (default: 1.0 = all)")
    parser.add_argument("--feast-repo", type=Path, default=DEFAULT_FEAST_REPO,
                        help=f"Path to Feast feature_repo (default: {DEFAULT_FEAST_REPO})")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH,
                        help=f"DuckDB path for cold-start fallback (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args()
    main(args.sample_frac, args.feast_repo, args.db_path)
