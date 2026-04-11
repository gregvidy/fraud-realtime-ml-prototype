"""
generate_reference_data.py
--------------------------
Generates synthetic reference entities (users, devices, merchants) and writes
them to Postgres. Run once before generate_historical_transactions.py.

Usage:
    python simulator/generate_reference_data.py [--n-users 2000] [--n-devices 4000]
                                                [--n-merchants 300] [--seed 42]
"""

import argparse
import os
import random
import string
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from faker import Faker

load_dotenv()

fake = Faker()

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


def upsert_dataframe(conn, table: str, df: pd.DataFrame, conflict_col: str) -> None:
    cols = list(df.columns)
    col_list = ", ".join(cols)
    placeholder = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholder}) "
        f"ON CONFLICT ({conflict_col}) DO NOTHING"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, df.values.tolist())
    conn.commit()
    print(f"  -> {len(df):,} rows inserted into {table}")


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

COUNTRIES = ["US", "GB", "CA", "AU", "DE", "FR", "SG", "PH", "IN", "BR"]
COUNTRY_WEIGHTS = [30, 10, 8, 7, 6, 6, 5, 5, 8, 5]

ACCOUNT_TYPES = ["standard", "premium", "business"]
PLATFORMS = ["iOS", "Android", "Web", "Desktop"]
MERCHANT_CATEGORIES = [
    "grocery", "electronics", "travel", "gambling", "crypto",
    "restaurant", "clothing", "telecom", "utilities", "gaming",
]
RISK_TIERS = ["low", "medium", "high"]
RISK_TIER_WEIGHTS_BY_CATEGORY = {
    "gambling": [10, 30, 60],
    "crypto": [10, 30, 60],
    "gaming": [20, 40, 40],
    "telecom": [40, 40, 20],
    "travel": [30, 50, 20],
    "grocery": [60, 30, 10],
    "electronics": [40, 40, 20],
    "restaurant": [60, 30, 10],
    "clothing": [60, 30, 10],
    "utilities": [70, 25, 5],
}

NOW = datetime.now(timezone.utc)


def make_users(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        uid = f"u_{i+1:06d}"
        signup_dt = NOW - timedelta(days=rng.randint(1, 365 * 4))
        country = rng.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0]
        rows.append({
            "user_id": uid,
            "email": fake.email(),
            "phone": fake.phone_number()[:32],
            "country_code": country,
            "signup_date": signup_dt.date().isoformat(),
            "account_type": rng.choices(ACCOUNT_TYPES, weights=[75, 20, 5])[0],
            "is_verified": rng.random() > 0.15,
            "event_timestamp": signup_dt.isoformat(),
            "ingestion_timestamp": NOW.isoformat(),
        })
    return rows


def make_devices(users: list[dict], avg_devices_per_user: float, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    d_id = 1
    for user in users:
        n_devices = max(1, int(rng.normalvariate(avg_devices_per_user, 0.8)))
        for _ in range(n_devices):
            country = user["country_code"]
            event_ts = datetime.fromisoformat(user["event_timestamp"]) + timedelta(
                days=rng.randint(0, 30)
            )
            rows.append({
                "device_id": f"d_{d_id:07d}",
                "user_id": user["user_id"],
                "device_fingerprint": "".join(
                    rng.choices(string.ascii_lowercase + string.digits, k=32)
                ),
                "platform": rng.choices(PLATFORMS, weights=[35, 40, 20, 5])[0],
                "os_version": f"{rng.randint(10, 17)}.{rng.randint(0, 9)}",
                "ip_address": fake.ipv4_public(),
                "country_code": country,
                "event_timestamp": event_ts.isoformat(),
                "ingestion_timestamp": NOW.isoformat(),
            })
            d_id += 1
    # Inject shared-device fraud signal: some devices linked to many users
    n_shared = max(1, len(users) // 50)
    shared_device_ids = [f"d_{i:07d}" for i in range(d_id, d_id + n_shared)]
    for sid in shared_device_ids:
        sample_users = rng.choices(users, k=rng.randint(3, 8))
        for user in sample_users:
            event_ts = NOW - timedelta(days=rng.randint(0, 60))
            rows.append({
                "device_id": sid,
                "user_id": user["user_id"],
                "device_fingerprint": "".join(
                    rng.choices(string.ascii_lowercase + string.digits, k=32)
                ),
                "platform": "Android",
                "os_version": "12.0",
                "ip_address": fake.ipv4_public(),
                "country_code": user["country_code"],
                "event_timestamp": event_ts.isoformat(),
                "ingestion_timestamp": NOW.isoformat(),
            })
    return rows


def make_merchants(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        cat = rng.choice(MERCHANT_CATEGORIES)
        country = rng.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0]
        rows.append({
            "merchant_id": f"m_{i+1:05d}",
            "merchant_name": fake.company()[:255],
            "merchant_category": cat,
            "country_code": country,
            "is_online": rng.random() > 0.4,
            "risk_tier": rng.choices(
                RISK_TIERS, weights=RISK_TIER_WEIGHTS_BY_CATEGORY.get(cat, [40, 40, 20])
            )[0],
            "event_timestamp": (NOW - timedelta(days=rng.randint(30, 365))).isoformat(),
            "ingestion_timestamp": NOW.isoformat(),
        })
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(n_users: int, n_devices_avg: float, n_merchants: int, seed: int) -> None:
    Faker.seed(seed)
    print(f"Generating reference data   seed={seed}")

    print("  Generating users...")
    users = make_users(n_users, seed)

    print("  Generating devices...")
    devices = make_devices(users, n_devices_avg, seed + 1)

    print("  Generating merchants...")
    merchants = make_merchants(n_merchants, seed + 2)

    print("Connecting to Postgres...")
    conn = get_conn()

    print("Writing users...")
    upsert_dataframe(conn, "raw_users", pd.DataFrame(users), "user_id")

    print("Writing devices...")
    upsert_dataframe(conn, "raw_devices", pd.DataFrame(devices), "device_event_id")

    print("Writing merchants...")
    upsert_dataframe(conn, "raw_merchants", pd.DataFrame(merchants), "merchant_id")

    conn.close()
    print("Done. Reference data loaded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-users", type=int, default=2000)
    parser.add_argument("--n-devices-avg", type=float, default=2.0)
    parser.add_argument("--n-merchants", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    main(args.n_users, args.n_devices_avg, args.n_merchants, args.seed)
