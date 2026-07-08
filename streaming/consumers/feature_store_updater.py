"""
streaming/consumers/feature_store_updater.py
--------------------------------------------
Reads txn.raw.<channel> events and updates Redis sliding-window features by
delegating to the existing `app.online_features.updater.update_online_features`
(the same code path the pre-Redpanda in-process simulator used).

This preserves the current Redis sorted-set schema so the scoring API doesn't
need to change: same keys, same ZADD/ZREMRANGEBYSCORE semantics.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow importing the existing app package + streaming package when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.online_features.updater import update_online_features  # noqa: E402
from streaming.config import (  # noqa: E402
    GROUP_FEATURE_STORE_UPDATER,
    REDPANDA_BROKER,
    SCHEMA_REGISTRY_URL,
    TXN_EVENT_SCHEMA_PATH,
)
from streaming.consumers.base import AsyncAvroConsumer, DecodedRecord  # noqa: E402

TOPIC_PATTERN = r"^txn\.raw\..+$"


def _to_updater_event(rec: DecodedRecord) -> dict:
    """Reshape the decoded Avro event to what update_online_features expects.

    The legacy updater accepts event_timestamp as ISO-string, float, or int.
    Our Avro payload gives us a `datetime` — convert to epoch float."""
    v = rec.value
    return {
        "transaction_id": v["transaction_id"],
        "user_id":        v["user_id"],
        "device_id":      v["device_id"],
        "merchant_id":    v["merchant_id"],
        "amount":         v["amount"],
        "txn_status":     v["txn_status"],
        "event_timestamp": v["event_timestamp"].timestamp(),
    }


async def _handle_batch(batch: list[DecodedRecord]) -> None:
    # update_online_features() is sync (uses redis.Redis, not redis.asyncio).
    # A batch of 500 events × ~4 pipelined Redis ops ≈ 2000 Redis roundtrips.
    # At <1ms each, that's <1s per batch — fine for POC. Consider redis.asyncio
    # + gather() if you push the producer past ~1000 eps.
    for rec in batch:
        update_online_features(_to_updater_event(rec))


async def run(*, duration: float = 0.0) -> None:
    consumer = AsyncAvroConsumer(
        name="feature_store_updater",
        group_id=GROUP_FEATURE_STORE_UPDATER,
        bootstrap_servers=REDPANDA_BROKER,
        schema_registry_url=SCHEMA_REGISTRY_URL,
        value_schema_path=TXN_EVENT_SCHEMA_PATH,
        topic_pattern=TOPIC_PATTERN,
        batch_size=500,
        batch_timeout_ms=500,
    )
    await consumer.run(_handle_batch, duration=duration)


if __name__ == "__main__":
    asyncio.run(run())
