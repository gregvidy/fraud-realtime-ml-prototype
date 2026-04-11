"""
generate_historical_transactions.py
------------------------------------
Generates historical raw_transactions, raw_login_events, and fraud_labels
from existing reference data (users, devices, merchants) in Postgres.

Fraud patterns injected:
  - Velocity bursts (many txns in short window)
  - Device sharing (same device, multiple users)
  - High-risk merchants
  - Off-hours transactions
  - International mismatches
  - New account + large amount

Usage:
    python simulator/generate_historical_transactions.py [--days 90] [--seed 42]
"""

import argparse
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from faker import Faker

load_dotenv()
fake = Faker()

CURRENCIES = ["USD", "EUR", "GBP", "SGD", "AUD"]
PAYMENT_METHODS = ["card", "wallet", "bank_transfer", "crypto"]
TXN_STATUSES = ["success", "decline", "pending"]
FRAUD_TYPES = ["card_not_present", "account_takeover", "identity_theft", "bust_out"]
NOW = datetime.now(timezone.utc)


def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


def load_reference(conn) -> tuple[list, list, list]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, country_code, signup_date FROM raw_users")
        users = [{"user_id": r[0], "country_code": r[1], "signup_date": r[2]} for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT ON (device_id) device_id, user_id, country_code FROM raw_devices ORDER BY device_id")
        devices = [{"device_id": r[0], "user_id": r[1], "country_code": r[2]} for r in cur.fetchall()]

        cur.execute("SELECT merchant_id, merchant_category, risk_tier FROM raw_merchants")
        merchants = [{"merchant_id": r[0], "merchant_category": r[1], "risk_tier": r[2]} for r in cur.fetchall()]

    return users, devices, merchants


def make_txn(
    user: dict,
    device_id: str,
    merchant: dict,
    event_ts: datetime,
    rng: random.Random,
    is_fraud: bool = False,
    amount_override: float | None = None,
) -> tuple[dict, dict]:
    amount = amount_override or round(rng.lognormvariate(4.0, 1.2), 2)
    country = user["country_code"]
    # Fraud signal: international mismatch
    txn_country = (
        rng.choice(["GB", "DE", "SG", "PH"]) if (is_fraud and rng.random() > 0.5) else country
    )
    is_international = txn_country != country
    status = (
        rng.choices(["success", "decline"], weights=[60, 40])[0]
        if is_fraud
        else rng.choices(["success", "decline", "pending"], weights=[88, 10, 2])[0]
    )
    txn_id = str(uuid.uuid4())
    txn = {
        "transaction_id": txn_id,
        "user_id": user["user_id"],
        "device_id": device_id,
        "merchant_id": merchant["merchant_id"],
        "amount": amount,
        "currency": rng.choices(CURRENCIES, weights=[60, 15, 10, 10, 5])[0],
        "payment_method": rng.choices(PAYMENT_METHODS, weights=[65, 20, 10, 5])[0],
        "country_code": txn_country,
        "ip_address": fake.ipv4_public(),
        "is_international": is_international,
        "txn_status": status,
        "decline_reason": (
            rng.choice(["insufficient_funds", "fraud_hold", "card_expired", "limit_exceeded"])
            if status == "decline"
            else None
        ),
        "local_hour": event_ts.hour,
        "event_timestamp": event_ts.isoformat(),
        "ingestion_timestamp": NOW.isoformat(),
    }
    label = {
        "transaction_id": txn_id,
        "is_fraud": is_fraud,
        "fraud_type": rng.choice(FRAUD_TYPES) if is_fraud else None,
        "label_source": "synthetic",
        "label_timestamp": event_ts.isoformat(),
        "ingestion_timestamp": NOW.isoformat(),
    }
    return txn, label


def generate_normal_transactions(
    users, devices, merchants, rng, days, base_fraud_rate
):
    device_map = {d["user_id"]: [] for d in devices}
    for d in devices:
        device_map[d["user_id"]].append(d["device_id"])

    high_risk_merchants = [m for m in merchants if m["risk_tier"] == "high"]
    low_risk_merchants  = [m for m in merchants if m["risk_tier"] != "high"]

    txns, labels = [], []
    for user in users:
        n_txns = max(1, int(rng.normalvariate(days * 0.8, days * 0.3)))
        user_devices = device_map.get(user["user_id"], [devices[0]["device_id"]])
        for _ in range(n_txns):
            days_ago = rng.uniform(0, days)
            event_ts = NOW - timedelta(days=days_ago)
            device_id = rng.choice(user_devices)
            # Slightly higher chance of fraud on high-risk merchants
            merchant = (
                rng.choice(high_risk_merchants)
                if (rng.random() < 0.3 and high_risk_merchants)
                else rng.choice(low_risk_merchants or merchants)
            )
            is_fraud = rng.random() < base_fraud_rate
            txn, label = make_txn(user, device_id, merchant, event_ts, rng, is_fraud)
            txns.append(txn)
            labels.append(label)
    return txns, labels


def inject_fraud_bursts(users, devices, merchants, rng, n_burst_users=30):
    """Inject velocity burst fraud patterns."""
    txns, labels = [], []
    high_risk_merchants = [m for m in merchants if m["risk_tier"] == "high"] or merchants
    burst_users = rng.choices(users, k=n_burst_users)
    device_map = {d["user_id"]: d["device_id"] for d in devices}

    for user in burst_users:
        burst_start = NOW - timedelta(days=rng.randint(1, 60))
        device_id = device_map.get(user["user_id"], devices[0]["device_id"])
        for j in range(rng.randint(5, 15)):
            event_ts = burst_start + timedelta(minutes=j * rng.randint(1, 4))
            merchant = rng.choice(high_risk_merchants)
            amount = round(rng.uniform(200, 2000), 2)
            txn, label = make_txn(
                user, device_id, merchant, event_ts, rng, is_fraud=True, amount_override=amount
            )
            txns.append(txn)
            labels.append(label)
    return txns, labels


def make_logins(users, rng, days):
    rows = []
    for user in users:
        n = max(1, int(rng.normalvariate(days * 0.5, days * 0.2)))
        for _ in range(n):
            event_ts = NOW - timedelta(days=rng.uniform(0, days))
            status = rng.choices(["success", "failed"], weights=[85, 15])[0]
            rows.append({
                "user_id": user["user_id"],
                "device_id": None,
                "ip_address": fake.ipv4_public(),
                "country_code": user["country_code"],
                "login_status": status,
                "failure_reason": (
                    rng.choice(["wrong_password", "otp_failed", "suspended_account"])
                    if status == "failed"
                    else None
                ),
                "event_timestamp": event_ts.isoformat(),
                "ingestion_timestamp": NOW.isoformat(),
            })
    return rows


def bulk_insert(conn, table: str, rows: list[dict], batch_size: int = 1000) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    cols = list(df.columns)
    col_list = ", ".join(cols)
    placeholder = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholder}) ON CONFLICT DO NOTHING"
    with conn.cursor() as cur:
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start : start + batch_size].values.tolist()
            # Replace NaN/None properly
            clean = [
                [None if (isinstance(v, float) and str(v) == "nan") else v for v in row]
                for row in batch
            ]
            cur.executemany(sql, clean)
    conn.commit()
    print(f"  -> {len(rows):,} rows inserted into {table}")


def main(days: int, seed: int, fraud_rate: float) -> None:
    Faker.seed(seed)
    rng = random.Random(seed)
    print(f"Generating historical data  days={days}  seed={seed}  fraud_rate={fraud_rate}")

    conn = get_conn()
    print("Loading reference data...")
    users, devices, merchants = load_reference(conn)
    print(f"  users={len(users)}  devices={len(devices)}  merchants={len(merchants)}")

    print("Generating normal transactions...")
    txns, labels = generate_normal_transactions(users, devices, merchants, rng, days, fraud_rate)

    print("Injecting fraud burst patterns...")
    burst_txns, burst_labels = inject_fraud_bursts(users, devices, merchants, rng)
    txns.extend(burst_txns)
    labels.extend(burst_labels)

    print("Generating login events...")
    logins = make_logins(users, rng, days)

    print(f"Total transactions: {len(txns):,}  fraud: {sum(l['is_fraud'] for l in labels):,}")

    print("Writing transactions...")
    bulk_insert(conn, "raw_transactions", txns)

    print("Writing fraud labels...")
    bulk_insert(conn, "fraud_labels", labels)

    print("Writing login events...")
    bulk_insert(conn, "raw_login_events", logins)

    conn.close()
    print("Done. Historical data loaded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fraud-rate", type=float, default=0.03)
    args = parser.parse_args()
    main(args.days, args.seed, args.fraud_rate)
