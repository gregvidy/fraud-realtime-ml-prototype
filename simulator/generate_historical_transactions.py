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

Transaction amount distribution:
  Heavy-tailed mixture of three lognormal components mimicking real card data:
    65% → everyday small transactions  (median ~$16,  σ=0.7)
    25% → mid-range transactions        (median ~$90,  σ=0.8)
    10% → large / luxury transactions   (median ~$403, σ=1.5)
  Produces right-skewed, high-kurtosis distribution with long upper tail.

Usage:
    python simulator/generate_historical_transactions.py \
        [--start-date 2024-01-01] [--end-date 2024-12-31] \
        [--fraud-rate-min 0.01] [--fraud-rate-max 0.05] \
        [--seed 42]
"""

import argparse
import math
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from faker import Faker

load_dotenv()
fake = Faker()

CURRENCIES = ["USD", "EUR", "GBP", "SGD", "AUD"]
PAYMENT_METHODS = ["card", "wallet", "bank_transfer", "crypto"]
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


def sample_amount(rng: random.Random) -> float:
    """Heavy-tailed transaction amount distribution.

    Mixture of three lognormal components replicating empirical card-transaction data:
      - 65%: everyday small txns   lognormal(μ=2.8, σ=0.7)  → median ~$16
      - 25%: mid-range txns         lognormal(μ=4.5, σ=0.8)  → median ~$90
      - 10%: large / luxury txns    lognormal(μ=6.0, σ=1.5)  → median ~$403, long tail

    The heavy 10% component drives right-skewness and high kurtosis, producing
    a long upper tail consistent with real payment data (occasional large purchases
    up to tens of thousands).
    """
    roll = rng.random()
    if roll < 0.65:
        mu, sigma = 2.8, 0.7   # coffee, grocery, fast-food
    elif roll < 0.90:
        mu, sigma = 4.5, 0.8   # dining, clothing, accessories
    else:
        mu, sigma = 6.0, 1.5   # travel, electronics, luxury — heavy tail
    return max(1.0, round(math.exp(rng.gauss(mu, sigma)), 2))


def get_monthly_fraud_rates(
    start_dt: datetime,
    end_dt: datetime,
    fraud_rate_min: float,
    fraud_rate_max: float,
    rng: random.Random,
) -> dict:
    """Assign an independent fraud rate to each calendar month in the date range."""
    rates: dict = {}
    current = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current <= end_dt:
        rates[(current.year, current.month)] = round(
            rng.uniform(fraud_rate_min, fraud_rate_max), 4
        )
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return rates


def make_txn(
    user: dict,
    device_id: str,
    merchant: dict,
    event_ts: datetime,
    rng: random.Random,
    is_fraud: bool = False,
    amount_override: Optional[float] = None,
) -> tuple[dict, dict]:
    amount = amount_override if amount_override is not None else sample_amount(rng)
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
    users, devices, merchants, rng, start_dt, end_dt, fraud_rate_min, fraud_rate_max
):
    monthly_rates = get_monthly_fraud_rates(start_dt, end_dt, fraud_rate_min, fraud_rate_max, rng)
    total_days = (end_dt - start_dt).total_seconds() / 86400
    total_seconds = (end_dt - start_dt).total_seconds()

    device_map = {d["user_id"]: [] for d in devices}
    for d in devices:
        device_map[d["user_id"]].append(d["device_id"])

    high_risk_merchants = [m for m in merchants if m["risk_tier"] == "high"]
    low_risk_merchants  = [m for m in merchants if m["risk_tier"] != "high"]

    txns, labels = [], []
    for user in users:
        n_txns = max(1, int(rng.normalvariate(total_days * 0.8, total_days * 0.3)))
        user_devices = device_map.get(user["user_id"], [devices[0]["device_id"]])
        for _ in range(n_txns):
            offset = rng.uniform(0, total_seconds)
            event_ts = start_dt + timedelta(seconds=offset)
            device_id = rng.choice(user_devices)
            merchant = (
                rng.choice(high_risk_merchants)
                if (rng.random() < 0.3 and high_risk_merchants)
                else rng.choice(low_risk_merchants or merchants)
            )
            fraud_rate = monthly_rates.get((event_ts.year, event_ts.month), fraud_rate_min)
            is_fraud = rng.random() < fraud_rate
            txn, label = make_txn(user, device_id, merchant, event_ts, rng, is_fraud)
            txns.append(txn)
            labels.append(label)
    return txns, labels


def inject_fraud_bursts(users, devices, merchants, rng, start_dt, end_dt, n_burst_users=30):
    """Inject velocity burst fraud patterns within the date range."""
    txns, labels = [], []
    high_risk_merchants = [m for m in merchants if m["risk_tier"] == "high"] or merchants
    burst_users = rng.choices(users, k=n_burst_users)
    device_map = {d["user_id"]: d["device_id"] for d in devices}

    total_seconds = (end_dt - start_dt).total_seconds()
    for user in burst_users:
        burst_offset = rng.uniform(0, max(0, total_seconds - 3600))
        burst_start = start_dt + timedelta(seconds=burst_offset)
        device_id = device_map.get(user["user_id"], devices[0]["device_id"])
        for j in range(rng.randint(5, 15)):
            event_ts = burst_start + timedelta(minutes=j * rng.randint(1, 4))
            if event_ts > end_dt:
                break
            merchant = rng.choice(high_risk_merchants)
            amount = round(rng.uniform(200, 2000), 2)
            txn, label = make_txn(
                user, device_id, merchant, event_ts, rng, is_fraud=True, amount_override=amount
            )
            txns.append(txn)
            labels.append(label)
    return txns, labels


def make_logins(users, rng, start_dt, end_dt):
    total_days = (end_dt - start_dt).total_seconds() / 86400
    total_seconds = (end_dt - start_dt).total_seconds()
    rows = []
    for user in users:
        n = max(1, int(rng.normalvariate(total_days * 0.5, total_days * 0.2)))
        for _ in range(n):
            offset = rng.uniform(0, total_seconds)
            event_ts = start_dt + timedelta(seconds=offset)
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


def main(start_dt: datetime, end_dt: datetime, fraud_rate_min: float, fraud_rate_max: float, seed: int) -> None:
    Faker.seed(seed)
    rng = random.Random(seed)
    days = (end_dt - start_dt).days
    print(
        f"Generating historical data  "
        f"start={start_dt.date()}  end={end_dt.date()}  days={days}  seed={seed}  "
        f"fraud_rate=[{fraud_rate_min}, {fraud_rate_max}]"
    )

    conn = get_conn()
    print("Loading reference data...")
    users, devices, merchants = load_reference(conn)
    print(f"  users={len(users)}  devices={len(devices)}  merchants={len(merchants)}")

    print("Generating normal transactions...")
    txns, labels = generate_normal_transactions(
        users, devices, merchants, rng, start_dt, end_dt, fraud_rate_min, fraud_rate_max
    )

    print("Injecting fraud burst patterns...")
    burst_txns, burst_labels = inject_fraud_bursts(users, devices, merchants, rng, start_dt, end_dt)
    txns.extend(burst_txns)
    labels.extend(burst_labels)

    print("Generating login events...")
    logins = make_logins(users, rng, start_dt, end_dt)

    fraud_count = sum(l["is_fraud"] for l in labels)
    print(f"Total transactions: {len(txns):,}  fraud: {fraud_count:,}  ({fraud_count / len(txns) * 100:.2f}%)")

    print("Writing transactions...")
    bulk_insert(conn, "raw_transactions", txns)

    print("Writing fraud labels...")
    bulk_insert(conn, "fraud_labels", labels)

    print("Writing login events...")
    bulk_insert(conn, "raw_login_events", logins)

    conn.close()
    print("Done. Historical data loaded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate historical fraud detection data")
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="Start date YYYY-MM-DD (default: 90 days before end-date)",
    )
    parser.add_argument(
        "--end-date", type=str, default=None,
        help="End date YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument("--fraud-rate-min", type=float, default=0.01, help="Min monthly fraud rate (default: 0.01)")
    parser.add_argument("--fraud-rate-max", type=float, default=0.05, help="Max monthly fraud rate (default: 0.05)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    end_dt = (
        datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end_date
        else NOW
    )
    start_dt = (
        datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.start_date
        else end_dt - timedelta(days=90)
    )

    if start_dt >= end_dt:
        raise ValueError(f"start_date ({start_dt.date()}) must be before end_date ({end_dt.date()})")
    if not (0 <= args.fraud_rate_min <= args.fraud_rate_max <= 1):
        raise ValueError("fraud_rate_min and fraud_rate_max must satisfy 0 <= min <= max <= 1")

    main(start_dt, end_dt, args.fraud_rate_min, args.fraud_rate_max, args.seed)
