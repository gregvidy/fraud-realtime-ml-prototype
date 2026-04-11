"""
stream_transactions.py
-----------------------
Simulates a real-time transaction event stream.
Each event is published in-process to the online feature updater (Redis).
Prints event summary to stdout at a configurable rate.

Usage:
    python simulator/stream_transactions.py [--eps 10] [--seed 42]
"""

import argparse
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from faker import Faker

# Allow importing from app/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()
fake = Faker()

CURRENCIES = ["USD", "EUR", "GBP", "SGD"]
PAYMENT_METHODS = ["card", "wallet", "bank_transfer"]


def get_dummy_reference(conn=None):
    """Return lightweight in-memory reference pools (or load from DB)."""
    if conn is None:
        # Minimal offline pool for standalone testing
        users     = [f"u_{i:06d}" for i in range(1, 101)]
        devices   = [f"d_{i:07d}" for i in range(1, 201)]
        merchants = [f"m_{i:05d}" for i in range(1, 51)]
        return users, devices, merchants

    import psycopg2
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM raw_users LIMIT 500")
        users = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT device_id FROM raw_devices LIMIT 1000")
        devices = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT merchant_id FROM raw_merchants LIMIT 200")
        merchants = [r[0] for r in cur.fetchall()]
    return users, devices, merchants


def make_event(user_id, device_id, merchant_id, rng, is_fraud=False):
    now = datetime.now(timezone.utc)
    amount = round(rng.lognormvariate(4.0, 1.2), 2)
    if is_fraud:
        amount = round(rng.uniform(300, 3000), 2)
    return {
        "transaction_id": str(uuid.uuid4()),
        "user_id": user_id,
        "device_id": device_id,
        "merchant_id": merchant_id,
        "amount": amount,
        "currency": rng.choice(CURRENCIES),
        "payment_method": rng.choice(PAYMENT_METHODS),
        "country_code": "US",
        "is_international": rng.random() < 0.08,
        "txn_status": rng.choices(["success", "decline"], weights=[90, 10])[0],
        "local_hour": now.hour,
        "event_timestamp": now.isoformat(),
        "is_fraud_sim": is_fraud,
    }


def main(eps: int, seed: int, fraud_rate: float, use_db: bool) -> None:
    Faker.seed(seed)
    rng = random.Random(seed)

    conn = None
    if use_db:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", 5432)),
                user=os.getenv("POSTGRES_USER", "fraud_user"),
                password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
                dbname=os.getenv("POSTGRES_DB", "fraud_db"),
            )
        except Exception as e:
            print(f"Warning: Could not connect to Postgres ({e}). Using dummy reference pool.")

    users, devices, merchants = get_dummy_reference(conn)

    try:
        from app.online_features.updater import update_online_features
        updater_available = True
    except ImportError:
        updater_available = False
        print("Warning: online feature updater not found — events will be printed only.")

    interval = 1.0 / eps
    print(f"Streaming {eps} events/sec  fraud_rate={fraud_rate}  Ctrl+C to stop")
    count = 0

    while True:
        start = time.monotonic()
        user_id    = rng.choice(users)
        device_id  = rng.choice(devices)
        merchant_id = rng.choice(merchants)
        is_fraud   = rng.random() < fraud_rate

        event = make_event(user_id, device_id, merchant_id, rng, is_fraud)

        if updater_available:
            try:
                update_online_features(event)
            except Exception as e:
                print(f"  Updater error: {e}")

        count += 1
        fraud_flag = "FRAUD" if is_fraud else "     "
        print(
            f"[{count:>7}] {fraud_flag}  {event['transaction_id'][:8]}  "
            f"user={user_id}  merchant={merchant_id}  amount={event['amount']:>9.2f}"
        )

        elapsed = time.monotonic() - start
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eps", type=int, default=int(os.getenv("SIM_EVENTS_PER_SECOND", 10)))
    parser.add_argument("--seed", type=int, default=int(os.getenv("SIM_SEED", 42)))
    parser.add_argument("--fraud-rate", type=float, default=float(os.getenv("SIM_FRAUD_RATE", 0.03)))
    parser.add_argument("--use-db", action="store_true", default=False)
    args = parser.parse_args()
    main(args.eps, args.seed, args.fraud_rate, args.use_db)
