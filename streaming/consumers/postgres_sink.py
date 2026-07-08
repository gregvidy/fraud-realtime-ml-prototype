"""
streaming/consumers/postgres_sink.py
------------------------------------
Reads txn.raw.<channel> events and batch-inserts them into
public.raw_transactions in Postgres via asyncpg's fast `copy_records_to_table`.

`ON CONFLICT (transaction_id) DO NOTHING` isn't used with COPY — instead we
COPY into a session-temp table then INSERT SELECT ... ON CONFLICT, which is
still faster than executemany and keeps at-least-once semantics idempotent.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from streaming.config import (  # noqa: E402
    GROUP_POSTGRES_SINK,
    REDPANDA_BROKER,
    SCHEMA_REGISTRY_URL,
    TXN_EVENT_SCHEMA_PATH,
)
from streaming.consumers.base import AsyncAvroConsumer, DecodedRecord  # noqa: E402

load_dotenv()

TOPIC_PATTERN = r"^txn\.raw\..+$"

INSERT_COLUMNS = (
    "transaction_id", "user_id", "device_id", "merchant_id",
    "amount", "currency", "payment_method", "country_code",
    "ip_address", "is_international", "txn_status", "local_hour",
    "event_timestamp",
)

# One-shot INSERT SELECT with dedup. `unnest($1::type[], $2::type[], ...)` is
# the classic asyncpg pattern for bulk insert without prepared statements.
INSERT_SQL = f"""
INSERT INTO raw_transactions ({", ".join(INSERT_COLUMNS)})
SELECT * FROM unnest(
    $1::text[], $2::text[], $3::text[], $4::text[],
    $5::numeric[], $6::text[], $7::text[], $8::text[],
    $9::inet[], $10::boolean[], $11::text[], $12::smallint[],
    $13::timestamptz[]
)
ON CONFLICT (transaction_id) DO NOTHING
"""


class PostgresSink:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=1, max_size=4, command_timeout=30
        )

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def write_batch(self, batch: list[DecodedRecord]) -> int:
        if not self._pool:
            raise RuntimeError("PostgresSink.start() must be called first")

        # Transpose batch → per-column arrays for the unnest() call.
        cols: dict[str, list] = {c: [] for c in INSERT_COLUMNS}
        for rec in batch:
            v = rec.value
            cols["transaction_id"].append(v["transaction_id"])
            cols["user_id"].append(v["user_id"])
            cols["device_id"].append(v["device_id"])
            cols["merchant_id"].append(v["merchant_id"])
            cols["amount"].append(v["amount"])
            cols["currency"].append(v["currency"])
            cols["payment_method"].append(v["payment_method"])
            cols["country_code"].append(v["country_code"])
            # Avro emits nullable string as {"string": "..."} or None after
            # confluent's AvroDeserializer flattens it, so it should be a
            # plain str or None here.
            cols["ip_address"].append(v.get("ip_address"))
            cols["is_international"].append(bool(v["is_international"]))
            cols["txn_status"].append(v["txn_status"])
            cols["local_hour"].append(int(v["local_hour"]))
            cols["event_timestamp"].append(v["event_timestamp"])

        async with self._pool.acquire() as conn:
            await conn.execute(INSERT_SQL, *[cols[c] for c in INSERT_COLUMNS])
        return len(batch)


async def run(*, duration: float = 0.0) -> None:
    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER', 'fraud_user')}"
        f":{os.getenv('POSTGRES_PASSWORD', 'fraud_pass')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}"
        f":{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB', 'fraud_db')}"
    )
    sink = PostgresSink(dsn)
    await sink.start()

    async def _handle(batch: list[DecodedRecord]) -> None:
        await sink.write_batch(batch)

    try:
        consumer = AsyncAvroConsumer(
            name="postgres_sink",
            group_id=GROUP_POSTGRES_SINK,
            bootstrap_servers=REDPANDA_BROKER,
            schema_registry_url=SCHEMA_REGISTRY_URL,
            value_schema_path=TXN_EVENT_SCHEMA_PATH,
            topic_pattern=TOPIC_PATTERN,
            batch_size=500,
            batch_timeout_ms=500,
        )
        await consumer.run(_handle, duration=duration)
    finally:
        await sink.stop()


if __name__ == "__main__":
    asyncio.run(run())
