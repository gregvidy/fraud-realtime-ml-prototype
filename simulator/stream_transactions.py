"""
stream_transactions.py
-----------------------
Simulates a real-time transaction event stream and publishes to Redpanda.

Each event is Avro-serialized and written to the `txn.raw.<channel>` topic
corresponding to its bank channel. Partition key = user_id, so all
transactions for one user land on the same partition and preserve ordering
end-to-end.

Channel mix (default: visa=35% / mastercard=25% / qris=20% / debit=10% /
amex=5% / digital=5%) mirrors an Indonesian tier-1 bank's approximate
transaction distribution. Each channel has its own amount profile,
international rate, and currency mix.

Downstream: `streaming/consumers/*` will read these topics.

Usage:
    python simulator/stream_transactions.py --eps 200
    python simulator/stream_transactions.py --eps 100 --duration 60 --seed 42
    python simulator/stream_transactions.py --channel-mix "visa=0.5,qris=0.5"
    python simulator/stream_transactions.py --dry-run   # generate but don't publish
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from faker import Faker

# Allow importing the streaming package when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streaming.config import (  # noqa: E402
    CHANNELS,
    RAW_TXN_TOPICS,
    REDPANDA_BROKER,
    SCHEMA_REGISTRY_URL,
    TXN_EVENT_SCHEMA_PATH,
)
from streaming.producer import AvroTxnProducer  # noqa: E402

load_dotenv()
fake = Faker()


# ── Per-channel behavioural profiles ────────────────────────────────────────
# Chosen to reflect realistic bank / channel patterns:
#   - amex has higher tickets and international share (corporate + travel)
#   - qris is Indonesia-domestic, small IDR amounts, high volume
#   - digital wallet has more device sharing (fraud signal — we bake it in via
#     amount noise for POC; real device-sharing simulation is Phase 2)
# ---------------------------------------------------------------------------

PAYMENT_METHODS: dict[str, tuple[str, ...]] = {
    "visa":       ("card",),
    "mastercard": ("card",),
    "amex":       ("card",),
    "qris":       ("qr", "wallet"),
    "debit":      ("card",),          # debit card via PIN
    "digital":    ("wallet", "bank_transfer"),
}


@dataclass(frozen=True)
class ChannelProfile:
    amount_mu: float          # lognormal μ (natural log of median)
    amount_sigma: float       # lognormal σ
    intl_rate: float          # P(cross-border)
    currency_weights: dict[str, float]


CHANNEL_PROFILES: dict[str, ChannelProfile] = {
    "visa":       ChannelProfile(3.5, 1.2, 0.12, {"USD": 0.5, "EUR": 0.25, "GBP": 0.15, "SGD": 0.10}),
    "mastercard": ChannelProfile(3.5, 1.2, 0.12, {"USD": 0.5, "EUR": 0.25, "GBP": 0.15, "SGD": 0.10}),
    "amex":       ChannelProfile(5.0, 1.0, 0.25, {"USD": 0.9, "EUR": 0.10}),
    "qris":       ChannelProfile(2.5, 0.7, 0.02, {"IDR": 1.0}),         # Indonesia
    "debit":      ChannelProfile(3.0, 0.9, 0.03, {"USD": 0.7, "SGD": 0.15, "MYR": 0.15}),
    "digital":    ChannelProfile(3.5, 1.1, 0.08, {"USD": 0.5, "SGD": 0.2, "MYR": 0.15, "IDR": 0.15}),
}

DEFAULT_MIX = "visa=0.35,mastercard=0.25,qris=0.20,debit=0.10,amex=0.05,digital=0.05"


def parse_channel_mix(mix_str: str) -> dict[str, float]:
    """Parse `visa=0.35,mastercard=0.25,...` into a normalised weight dict."""
    weights: dict[str, float] = {}
    for pair in mix_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        try:
            name, w = pair.split("=")
        except ValueError:
            raise SystemExit(f"Bad --channel-mix entry {pair!r} — expected 'name=weight'")
        name = name.strip()
        if name not in CHANNELS:
            raise SystemExit(f"Unknown channel {name!r}. Valid: {', '.join(CHANNELS)}")
        weights[name] = float(w)
    if not weights:
        raise SystemExit("--channel-mix produced no channels")
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def _pick_weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights.keys())
    vals = [weights[k] for k in keys]
    return rng.choices(keys, weights=vals, k=1)[0]


def _load_reference(use_db: bool):
    """Return (users, devices, merchants). If use_db, pull from Postgres."""
    if not use_db:
        # Tiny in-memory pool for standalone testing without Postgres.
        return (
            [f"u_{i:06d}" for i in range(1, 501)],
            [f"d_{i:07d}" for i in range(1, 1001)],
            [f"m_{i:05d}" for i in range(1, 101)],
        )
    import psycopg2
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER", "fraud_user"),
        password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
        dbname=os.getenv("POSTGRES_DB", "fraud_db"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM raw_users LIMIT 2000")
            users = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT device_id FROM raw_devices LIMIT 5000")
            devices = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT merchant_id FROM raw_merchants LIMIT 500")
            merchants = [r[0] for r in cur.fetchall()]
        return users, devices, merchants
    finally:
        conn.close()


def make_event(
    channel: str,
    user_id: str,
    device_id: str,
    merchant_id: str,
    rng: random.Random,
    is_fraud: bool,
) -> tuple[str, str, dict]:
    """Build one Avro-shaped event dict. Returns (topic, key, value)."""
    profile = CHANNEL_PROFILES[channel]

    # Fraud events skew larger; overrides the channel's normal amount profile.
    if is_fraud:
        amount = round(rng.uniform(300, 5000), 2)
    else:
        raw = rng.lognormvariate(profile.amount_mu, profile.amount_sigma)
        amount = round(max(1.0, raw), 2)

    now = datetime.now(timezone.utc)
    txn_status = rng.choices(
        ["success", "decline", "pending"],
        weights=([60, 35, 5] if is_fraud else [90, 8, 2]),
    )[0]

    event = {
        "transaction_id": str(uuid.uuid4()),
        "user_id":        user_id,
        "device_id":      device_id,
        "merchant_id":    merchant_id,
        "channel":        channel,
        "amount":         float(amount),
        "currency":       _pick_weighted(rng, profile.currency_weights),
        "payment_method": rng.choice(PAYMENT_METHODS[channel]),
        "country_code":   "US",       # anchor country; is_international flips it downstream
        "is_international": rng.random() < profile.intl_rate,
        "txn_status":     txn_status,
        "local_hour":     now.hour,
        "event_timestamp": now,        # fastavro converts datetime → timestamp-micros
        "ip_address":     fake.ipv4_public(),
        "is_fraud_sim":   is_fraud,
    }
    return RAW_TXN_TOPICS[channel], user_id, event


def _on_delivery(err, msg):
    """Producer delivery callback. Only logs errors — happy path is silent."""
    if err is not None:
        print(f"  [delivery-error] topic={msg.topic()} err={err}", file=sys.stderr)


def run(
    eps: int,
    duration: int,
    seed: int,
    fraud_rate: float,
    channel_mix: dict[str, float],
    use_db: bool,
    dry_run: bool,
) -> None:
    Faker.seed(seed)
    rng = random.Random(seed)

    users, devices, merchants = _load_reference(use_db)
    print(
        f"Reference pool: users={len(users):,} devices={len(devices):,} "
        f"merchants={len(merchants):,}"
    )

    producer: AvroTxnProducer | None = None
    if not dry_run:
        producer = AvroTxnProducer(
            bootstrap_servers=REDPANDA_BROKER,
            schema_registry_url=SCHEMA_REGISTRY_URL,
            value_schema_path=TXN_EVENT_SCHEMA_PATH,
            client_id=f"fraud-sim-{os.getpid()}",
        )
        print(f"Publishing to Redpanda at {REDPANDA_BROKER}")
    else:
        print("Dry run — generating events but not publishing.")

    interval = 1.0 / eps if eps > 0 else 0.0
    print(
        f"Rate: {eps} eps  fraud_rate: {fraud_rate:.3f}  "
        f"duration: {'infinite' if duration == 0 else f'{duration}s'}  "
        f"channel_mix: {', '.join(f'{k}={v:.2f}' for k, v in channel_mix.items())}"
    )

    per_channel = Counter[str]()
    total = 0
    fraud_total = 0
    started = time.monotonic()

    try:
        while True:
            loop_start = time.monotonic()

            if duration and (loop_start - started) >= duration:
                break

            channel = _pick_weighted(rng, channel_mix)
            user_id = rng.choice(users)
            device_id = rng.choice(devices)
            merchant_id = rng.choice(merchants)
            is_fraud = rng.random() < fraud_rate

            topic, key, event = make_event(
                channel, user_id, device_id, merchant_id, rng, is_fraud
            )

            if producer is not None:
                producer.publish(topic, key, event, on_delivery=_on_delivery)

            total += 1
            per_channel[channel] += 1
            if is_fraud:
                fraud_total += 1

            if total % 1000 == 0:
                elapsed = loop_start - started
                actual_eps = total / elapsed if elapsed > 0 else 0
                mix = ", ".join(
                    f"{c}={per_channel[c]}" for c in sorted(per_channel)
                )
                print(
                    f"  [{total:>8,}] elapsed={elapsed:5.1f}s  actual_eps={actual_eps:6.1f}  "
                    f"fraud={fraud_total}  ({mix})"
                )

            # Pace the loop. Skip the sleep for eps == 0 (unbounded).
            if interval:
                sleep = interval - (time.monotonic() - loop_start)
                if sleep > 0:
                    time.sleep(sleep)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if producer is not None:
            remaining = producer.flush(timeout=15)
            if remaining:
                print(f"  WARN: {remaining} messages still queued after flush timeout")
        elapsed = time.monotonic() - started
        print(
            f"\nDone. published={total:,} fraud_injected={fraud_total} "
            f"elapsed={elapsed:.1f}s  avg_eps={total / max(elapsed, 1e-9):.1f}"
        )
        for ch in sorted(per_channel):
            pct = per_channel[ch] / total * 100 if total else 0
            print(f"  {ch:<12} {per_channel[ch]:>7,}  ({pct:5.2f}%)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Publish synthetic multi-channel transaction events to Redpanda")
    p.add_argument("--eps", type=int, default=int(os.getenv("SIM_EVENTS_PER_SECOND", 100)),
                   help="Target events per second (0 = unbounded)")
    p.add_argument("--duration", type=int, default=0,
                   help="Seconds to run (0 = until Ctrl+C)")
    p.add_argument("--seed", type=int, default=int(os.getenv("SIM_SEED", 42)))
    p.add_argument("--fraud-rate", type=float, default=float(os.getenv("SIM_FRAUD_RATE", 0.03)),
                   help="P(inject synthetic fraud) per event")
    p.add_argument("--channel-mix", type=str, default=DEFAULT_MIX,
                   help='Comma-separated "channel=weight" pairs; weights are auto-normalised')
    p.add_argument("--use-db", action="store_true",
                   help="Load user/device/merchant IDs from Postgres (default: in-memory pool)")
    p.add_argument("--dry-run", action="store_true",
                   help="Generate events + stats, but do NOT publish to Redpanda")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run(
        eps=args.eps,
        duration=args.duration,
        seed=args.seed,
        fraud_rate=args.fraud_rate,
        channel_mix=parse_channel_mix(args.channel_mix),
        use_db=args.use_db,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
