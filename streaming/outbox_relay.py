"""
streaming/outbox_relay.py — Slice 10.

Polls the `outbox_events` Postgres table and publishes unpublished rows to
Redpanda. Runs as a long-lived service; safe to run in multiple replicas
concurrently thanks to `FOR UPDATE SKIP LOCKED` (no duplicate publishes).

Design notes
~~~~~~~~~~~~
* Idempotent Kafka producer (`enable_idempotence=True`) — retries on transient
  broker errors don't create duplicates on the same partition.
* Two-phase per batch:
    1. BEGIN; SELECT ... FOR UPDATE SKIP LOCKED LIMIT N;
       (holds row-locks until we UPDATE published_at)
    2. `producer.send_and_wait` for each row → gather → wait for broker acks
    3. UPDATE published_at = NOW() WHERE id = ANY($1); COMMIT.
    On aiokafka error, the transaction rolls back → row stays unclaimed for
    next relay iteration.
* Publishes RAW BYTES (`avro_value` from the outbox) — no re-serialization.
  Consumers see identical wire format to a direct-produce.
* Signal handlers (SIGINT/SIGTERM) trigger graceful drain of the current
  batch before exit.

Usage:
  python -m streaming.outbox_relay
  python -m streaming.outbox_relay --duration 30   # exit after 30s
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import asyncpg
from aiokafka import AIOKafkaProducer
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streaming.config import REDPANDA_BROKER  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [outbox_relay] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


SELECT_BATCH_SQL = """
SELECT id, topic, partition_key, avro_value
FROM outbox_events
WHERE published_at IS NULL
ORDER BY id
LIMIT $1
FOR UPDATE SKIP LOCKED
"""

MARK_PUBLISHED_SQL = """
UPDATE outbox_events
SET published_at = NOW()
WHERE id = ANY($1::bigint[])
"""


async def _relay_batch(pool: asyncpg.Pool, producer: AIOKafkaProducer, batch_size: int) -> int:
    """Fetch → publish → mark. Returns number of rows published this iteration."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_BATCH_SQL, batch_size)
            if not rows:
                return 0

            # Publish all rows concurrently; each send_and_wait waits for broker ack
            futures = [
                producer.send_and_wait(
                    r["topic"],
                    value=bytes(r["avro_value"]),
                    key=r["partition_key"].encode("utf-8"),
                )
                for r in rows
            ]
            # If ANY publish fails, gather raises → the outer transaction rolls
            # back → rows stay unpublished for the next iteration
            await asyncio.gather(*futures)

            # All publishes acked — mark rows as published (still inside the tx)
            await conn.execute(MARK_PUBLISHED_SQL, [r["id"] for r in rows])

    return len(rows)


async def run(batch_size: int, poll_interval: float, duration: int) -> None:
    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER', 'fraud_user')}"
        f":{os.getenv('POSTGRES_PASSWORD', 'fraud_pass')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}"
        f":{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB', 'fraud_db')}"
    )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=30)

    producer = AIOKafkaProducer(
        bootstrap_servers=os.getenv("REDPANDA_BROKER", REDPANDA_BROKER),
        client_id="outbox-relay",
        enable_idempotence=True,
        acks="all",
        compression_type="snappy",
        linger_ms=20,
        max_batch_size=65536,
    )
    await producer.start()

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    log.info("started  broker=%s batch_size=%d poll_interval=%.3fs duration=%s",
             os.getenv("REDPANDA_BROKER", REDPANDA_BROKER),
             batch_size, poll_interval,
             f"{duration}s" if duration else "∞")

    started = time.perf_counter()
    total = 0
    last_log = started

    try:
        while not stop.is_set():
            elapsed = time.perf_counter() - started
            if duration and elapsed >= duration:
                log.info("duration=%ds reached, stopping", duration)
                break

            n = await _relay_batch(pool, producer, batch_size)
            total += n

            now = time.perf_counter()
            if n == 0:
                # backlog drained — small sleep before re-polling
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    pass
            elif now - last_log > 1.0:
                log.info("published=%d elapsed=%.1fs eps=%.1f",
                         total, elapsed, total / max(elapsed, 0.001))
                last_log = now
    finally:
        await producer.stop()
        await pool.close()
        elapsed = time.perf_counter() - started
        log.info("STOPPED  published=%d elapsed=%.1fs avg_eps=%.1f",
                 total, elapsed, total / max(elapsed, 0.001))


def main() -> None:
    p = argparse.ArgumentParser(description="Relay outbox_events to Redpanda")
    p.add_argument("--batch-size", type=int, default=500,
                   help="max rows per relay iteration")
    p.add_argument("--poll-interval", type=float, default=0.1,
                   help="sleep (s) when the outbox is empty before polling again")
    p.add_argument("--duration", type=int, default=0,
                   help="seconds to run before exiting (0 = forever)")
    args = p.parse_args()

    asyncio.run(run(
        batch_size=args.batch_size,
        poll_interval=args.poll_interval,
        duration=args.duration,
    ))


if __name__ == "__main__":
    main()
