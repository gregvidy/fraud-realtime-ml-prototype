"""
generate_warmup_data.py
-----------------------
Generates synthetic raw_transactions, fraud_labels, and raw_login_events
for a specified date range, using existing reference data (users, devices,
merchants) already in Postgres.

Purpose:
    Backfill warm-up data so that rolling-window features (1d / 7d / 30d)
    computed by dbt are fully populated at the start of the training period.
    Typical usage: generate October–November 2025 data before training on
    December 2025 – February 2026.

Usage:
    python simulator/generate_warmup_data.py \
        --start-date 2025-10-01 \
        --end-date   2025-12-01 \
        --seed 99 \
        --fraud-rate 0.025
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
FRAUD_TYPES = ["card_not_present", "account_takeover", "identity_theft", "bust_out"]
INGEST_TS = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5452)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )


def load_reference(conn) -> tuple[list, list, list]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, country_code, signup_date FROM raw_users")
        users = [{"user_id": r[0], "country_code": r[1], "signup_date": r[2]} for r in cur.fetchall()]

        cur.execute(
            "SELECT DISTINCT ON (device_id) device_id, user_id, country_code "
            "FROM raw_devices ORDER BY device_id"
        )
        devices = [{"device_id": r[0], "user_id": r[1], "country_code": r[2]} for r in cur.fetchall()]

        cur.execute("SELECT merchant_id, merchant_category, risk_tier FROM raw_merchants")
        merchants = [{"merchant_id": r[0], "merchant_category": r[1], "risk_tier": r[2]} for r in cur.fetchall()]

    return users, devices, merchants


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
            clean = [
                [None if (isinstance(v, float) and str(v) == "nan") else v for v in row]
                for row in batch
            ]
            cur.executemany(sql, clean)
    conn.commit()
    print(f"  -> {len(rows):,} rows inserted into {table}")


# ---------------------------------------------------------------------------
# Event timestamp helpers
# ---------------------------------------------------------------------------

def rand_ts(rng: random.Random, start_dt: datetime, end_dt: datetime) -> datetime:
    """Return a random UTC datetime uniformly within [start_dt, end_dt)."""
    total_seconds = (end_dt - start_dt).total_seconds()
    offset = timedelta(seconds=rng.uniform(0, total_seconds))
    return start_dt + offset


def rand_ts_within(rng: random.Random, anchor: datetime, window_hours: float) -> datetime:
    """Return a random datetime within anchor + [0, window_hours)."""
    return anchor + timedelta(seconds=rng.uniform(0, window_hours * 3600))


# ---------------------------------------------------------------------------
# Transaction / label generators
# ---------------------------------------------------------------------------

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
        "ingestion_timestamp": INGEST_TS,
    }
    label = {
        "transaction_id": txn_id,
        "is_fraud": is_fraud,
        "fraud_type": rng.choice(FRAUD_TYPES) if is_fraud else None,
        "label_source": "synthetic",
        "label_timestamp": event_ts.isoformat(),
        "ingestion_timestamp": INGEST_TS,
    }
    return txn, label


def generate_normal_transactions(
    users: list,
    devices: list,
    merchants: list,
    rng: random.Random,
    start_dt: datetime,
    end_dt: datetime,
    base_fraud_rate: float,
) -> tuple[list, list]:
    total_days = (end_dt - start_dt).days or 1

    device_map: dict[str, list[str]] = {d["user_id"]: [] for d in devices}
    for d in devices:
        device_map[d["user_id"]].append(d["device_id"])

    high_risk = [m for m in merchants if m["risk_tier"] == "high"]
    low_risk  = [m for m in merchants if m["risk_tier"] != "high"]

    txns, labels = [], []
    for user in users:
        # Scale transaction count proportionally to window size
        n_txns = max(1, int(rng.normalvariate(total_days * 0.8, total_days * 0.3)))
        user_devices = device_map.get(user["user_id"]) or [devices[0]["device_id"]]
        for _ in range(n_txns):
            event_ts = rand_ts(rng, start_dt, end_dt)
            device_id = rng.choice(user_devices)
            merchant = (
                rng.choice(high_risk)
                if (rng.random() < 0.3 and high_risk)
                else rng.choice(low_risk or merchants)
            )
            is_fraud = rng.random() < base_fraud_rate
            txn, label = make_txn(user, device_id, merchant, event_ts, rng, is_fraud)
            txns.append(txn)
            labels.append(label)
    return txns, labels


def inject_fraud_bursts(
    users: list,
    devices: list,
    merchants: list,
    rng: random.Random,
    start_dt: datetime,
    end_dt: datetime,
    n_burst_users: int = 30,
) -> tuple[list, list]:
    """Inject velocity burst fraud patterns within the date window."""
    txns, labels = [], []
    high_risk = [m for m in merchants if m["risk_tier"] == "high"] or merchants
    device_map = {d["user_id"]: d["device_id"] for d in devices}
    burst_users = rng.choices(users, k=n_burst_users)

    for user in burst_users:
        # Anchor burst start within the window (leave at least 1h of room)
        latest_anchor = end_dt - timedelta(hours=1)
        if latest_anchor <= start_dt:
            continue
        burst_start = rand_ts(rng, start_dt, latest_anchor)
        device_id = device_map.get(user["user_id"]) or devices[0]["device_id"]

        for j in range(rng.randint(5, 15)):
            event_ts = burst_start + timedelta(minutes=j * rng.randint(1, 4))
            if event_ts >= end_dt:
                break
            merchant = rng.choice(high_risk)
            amount = round(rng.uniform(200, 2000), 2)
            txn, label = make_txn(
                user, device_id, merchant, event_ts, rng,
                is_fraud=True, amount_override=amount,
            )
            txns.append(txn)
            labels.append(label)
    return txns, labels


def make_logins(
    users: list,
    rng: random.Random,
    start_dt: datetime,
    end_dt: datetime,
) -> list[dict]:
    total_days = (end_dt - start_dt).days or 1
    rows = []
    for user in users:
        n = max(1, int(rng.normalvariate(total_days * 0.5, total_days * 0.2)))
        for _ in range(n):
            event_ts = rand_ts(rng, start_dt, end_dt)
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
                "ingestion_timestamp": INGEST_TS,
            })
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(start_date: str, end_date: str, seed: int, fraud_rate: float) -> None:
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    if end_dt <= start_dt:
        raise ValueError(f"end-date ({end_date}) must be after start-date ({start_date})")

    total_days = (end_dt - start_dt).days
    print(
        f"Generating warm-up data\n"
        f"  window : {start_date} → {end_date}  ({total_days} days)\n"
        f"  seed   : {seed}\n"
        f"  fraud% : {fraud_rate * 100:.1f}%"
    )

    Faker.seed(seed)
    rng = random.Random(seed)

    conn = get_conn()
    print("\nLoading reference data from Postgres...")
    users, devices, merchants = load_reference(conn)
    print(f"  users={len(users):,}  devices={len(devices):,}  merchants={len(merchants):,}")

    print("\nGenerating normal transactions...")
    txns, labels = generate_normal_transactions(
        users, devices, merchants, rng, start_dt, end_dt, fraud_rate
    )

    print("Injecting fraud burst patterns...")
    burst_txns, burst_labels = inject_fraud_bursts(
        users, devices, merchants, rng, start_dt, end_dt
    )
    txns.extend(burst_txns)
    labels.extend(burst_labels)

    print("Generating login events...")
    logins = make_logins(users, rng, start_dt, end_dt)

    fraud_count = sum(l["is_fraud"] for l in labels)
    print(
        f"\nSummary: {len(txns):,} transactions  "
        f"fraud={fraud_count:,} ({fraud_count / max(len(txns), 1) * 100:.2f}%)  "
        f"logins={len(logins):,}"
    )

    print("\nWriting to Postgres (ON CONFLICT DO NOTHING — safe to re-run)...")
    bulk_insert(conn, "raw_transactions", txns)
    bulk_insert(conn, "fraud_labels", labels)
    bulk_insert(conn, "raw_login_events", logins)

    conn.close()
    print("\nDone. Warm-up data loaded successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic warm-up data for a date range.")
    parser.add_argument(
        "--start-date",
        default="2025-10-01",
        help="Inclusive start date (YYYY-MM-DD, UTC). Default: 2025-10-01",
    )
    parser.add_argument(
        "--end-date",
        default="2025-12-01",
        help="Exclusive end date (YYYY-MM-DD, UTC). Default: 2025-12-01",
    )
    parser.add_argument("--seed", type=int, default=99, help="Random seed (default 99)")
    parser.add_argument(
        "--fraud-rate", type=float, default=0.025,
        help="Base fraud rate 0–1 (default 0.025 = 2.5%%)",
    )
    args = parser.parse_args()
    main(args.start_date, args.end_date, args.seed, args.fraud_rate)
