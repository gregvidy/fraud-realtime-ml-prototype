"""
streaming/outbox_producer.py — Slice 10.

Emulates a Predator microservice that writes a transaction to raw_transactions
AND an outbox row in the SAME Postgres transaction. Demonstrates the
transactional-outbox pattern: DB commit == outbox row exists == event will be
delivered downstream (exactly once, modulo idempotent consumers).

Contrast with `simulator/stream_transactions.py`:
  stream_transactions.py → produces DIRECTLY to Redpanda (no DB write).
  outbox_producer.py     → writes to Postgres only; a separate outbox-relay
                           service picks up the outbox row and publishes.

Why: no dual-write risk. If the DB commit succeeds but the app crashes before
publishing, the relay still finds the row. If the DB rollback fires, the row
never exists.

Usage:
  python -m streaming.outbox_producer --eps 100 --duration 5 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import signal
import struct
import sys
import time
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from confluent_kafka.schema_registry import SchemaRegistryClient  # noqa: E402
from confluent_kafka.schema_registry.avro import AvroSerializer  # noqa: E402
from confluent_kafka.serialization import MessageField, SerializationContext  # noqa: E402

from simulator.stream_transactions import (  # noqa: E402
    make_event,
    _load_reference,
    _pick_weighted,
    parse_channel_mix,
    DEFAULT_MIX,
)
from streaming.config import (  # noqa: E402
    SCHEMA_REGISTRY_URL,
    TXN_EVENT_SCHEMA_PATH,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [outbox_producer] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# Serializes Avro payload identical to AvroTxnProducer — same on-the-wire bytes
# so the relay's publish is indistinguishable from a direct-produce.
def _make_serializer() -> AvroSerializer:
    sr = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})
    schema_str = Path(TXN_EVENT_SCHEMA_PATH).read_text()
    return AvroSerializer(sr, schema_str)


def _serialize_avro(ser: AvroSerializer, topic: str, event: dict) -> tuple[bytes, int]:
    """Serialize event to Confluent-wire-format bytes. Returns (payload, schema_id).
    Confluent wire format: 0x00 (magic) + 4-byte big-endian schema_id + Avro payload.
    """
    payload = ser(event, SerializationContext(topic, MessageField.VALUE))
    # bytes[0] is magic 0x00, bytes[1:5] is schema_id (big-endian, unsigned)
    schema_id = struct.unpack(">I", payload[1:5])[0]
    return payload, schema_id


# raw_transactions is the same table postgres_sink writes to. Using the SAME
# insert shape keeps semantics consistent — the outbox path is a drop-in
# replacement for direct producer + postgres_sink downstream.
INSERT_TXN_SQL = """
INSERT INTO raw_transactions (
    transaction_id, user_id, device_id, merchant_id,
    amount, currency, payment_method, country_code,
    ip_address, is_international, txn_status, local_hour,
    event_timestamp
) VALUES (
    $1, $2, $3, $4,
    $5, $6, $7, $8,
    $9::inet, $10, $11, $12,
    $13
)
ON CONFLICT (transaction_id) DO NOTHING
"""

INSERT_OUTBOX_SQL = """
INSERT INTO outbox_events (aggregate_id, topic, partition_key, avro_value, schema_id)
VALUES ($1, $2, $3, $4, $5)
"""


async def _write_batch(
    conn: asyncpg.Connection,
    events: list[tuple[str, str, dict, bytes, int]],
) -> None:
    """Write a batch of (topic, key, event, avro_bytes, schema_id) tuples in ONE tx.
    Two INSERTs per event: raw_transactions and outbox_events. If either fails,
    the whole transaction rolls back — outbox stays consistent with source data.
    """
    async with conn.transaction():
        for topic, key, event, avro_bytes, schema_id in events:
            await conn.execute(
                INSERT_TXN_SQL,
                event["transaction_id"], event["user_id"], event["device_id"], event["merchant_id"],
                event["amount"], event["currency"], event["payment_method"], event["country_code"],
                event.get("ip_address"), event["is_international"], event["txn_status"], event["local_hour"],
                event["event_timestamp"],
            )
            await conn.execute(
                INSERT_OUTBOX_SQL,
                event["transaction_id"], topic, key, avro_bytes, schema_id,
            )


async def run(eps: int, duration: int, seed: int, fraud_rate: float,
              channel_mix: dict[str, float], batch_size: int) -> None:
    users, devices, merchants = _load_reference(use_db=False)
    log.info("Reference pool: users=%d devices=%d merchants=%d",
             len(users), len(devices), len(merchants))

    rng = random.Random(seed)
    serializer = _make_serializer()

    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER', 'fraud_user')}"
        f":{os.getenv('POSTGRES_PASSWORD', 'fraud_pass')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}"
        f":{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB', 'fraud_db')}"
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=30)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    log.info("eps=%d duration=%ds seed=%d batch_size=%d fraud_rate=%.3f",
             eps, duration, seed, batch_size, fraud_rate)

    started = time.perf_counter()
    published = 0
    interval = batch_size / eps  # seconds between batches

    try:
        while not stop.is_set():
            elapsed = time.perf_counter() - started
            if duration and elapsed >= duration:
                break

            # Build a batch
            batch: list[tuple[str, str, dict, bytes, int]] = []
            for _ in range(batch_size):
                channel = _pick_weighted(rng, channel_mix)
                user_id = rng.choice(users)
                device_id = rng.choice(devices)
                merchant_id = rng.choice(merchants)
                is_fraud = rng.random() < fraud_rate
                topic, key, event = make_event(channel, user_id, device_id, merchant_id, rng, is_fraud)
                avro_bytes, schema_id = _serialize_avro(serializer, topic, event)
                batch.append((topic, key, event, avro_bytes, schema_id))

            async with pool.acquire() as conn:
                await _write_batch(conn, batch)

            published += len(batch)

            if published % 1000 < batch_size:
                cur_eps = published / max(elapsed, 0.001)
                log.info("written=%d elapsed=%.1fs eps=%.1f", published, elapsed, cur_eps)

            # Simple pacing — sleep the remainder of the interval
            target = started + (published / eps)
            sleep_for = target - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
    finally:
        elapsed = time.perf_counter() - started
        await pool.close()
        log.info("DONE  written=%d elapsed=%.1fs avg_eps=%.1f",
                 published, elapsed, published / max(elapsed, 0.001))


def main() -> None:
    p = argparse.ArgumentParser(description="Dual-write transaction events via outbox pattern")
    p.add_argument("--eps", type=int, default=100)
    p.add_argument("--duration", type=int, default=0, help="seconds (0 = until Ctrl+C)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--fraud-rate", type=float, default=0.03)
    p.add_argument("--channel-mix", type=str, default=DEFAULT_MIX)
    p.add_argument("--batch-size", type=int, default=50,
                   help="events per DB transaction (bigger = fewer commits, higher latency per batch)")
    args = p.parse_args()

    mix = parse_channel_mix(args.channel_mix)
    asyncio.run(run(
        eps=args.eps,
        duration=args.duration,
        seed=args.seed,
        fraud_rate=args.fraud_rate,
        channel_mix=mix,
        batch_size=args.batch_size,
    ))


if __name__ == "__main__":
    main()
