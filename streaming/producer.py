"""
streaming/producer.py — Avro-serializing Kafka producer for txn events.

Wraps `confluent_kafka.Producer` with a Schema-Registry-backed Avro serializer
so callers can hand it plain Python dicts and get schema-validated wire bytes.

Key serialization: plain UTF-8 string (partitioning key = user_id).
Value serialization: Avro against the schema registered for the destination
topic's `-value` subject.

Every produce triggers `producer.poll(0)` so delivery callbacks fire promptly;
call `flush()` before shutdown to drain the in-memory queue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import (
    MessageField,
    SerializationContext,
    StringSerializer,
)

DeliveryCallback = Callable[[Any, Any], None] | None


class AvroTxnProducer:
    """Publish dicts to a Redpanda topic as Avro-encoded messages.

    One instance can publish to multiple topics as long as they all use the
    SAME value schema (e.g. all 6 `txn.raw.<channel>` topics share TxnEvent).
    For a different value schema (ScoredTxnEvent, LoginEvent), instantiate a
    second producer.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        schema_registry_url: str,
        value_schema_path: Path,
        *,
        client_id: str = "fraud-sim-producer",
    ) -> None:
        sr = SchemaRegistryClient({"url": schema_registry_url})
        schema_str = Path(value_schema_path).read_text()

        self._value_ser = AvroSerializer(sr, schema_str)
        self._key_ser = StringSerializer("utf_8")

        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "client.id": client_id,
            # idempotent + acks=all → exactly-once (well, at-least-once with
            # dedup) semantics per topic-partition. Cheap on a single-node
            # broker; matters when we scale to 3 nodes.
            "enable.idempotence": True,
            "acks": "all",
            "compression.type": "snappy",
            "linger.ms": 20,
            "batch.size": 65536,
        })

    def publish(
        self,
        topic: str,
        key: str,
        value: dict,
        on_delivery: DeliveryCallback = None,
    ) -> None:
        """Enqueue a message. Serialization + network send happen async."""
        self._producer.produce(
            topic=topic,
            key=self._key_ser(key, SerializationContext(topic, MessageField.KEY)),
            value=self._value_ser(value, SerializationContext(topic, MessageField.VALUE)),
            on_delivery=on_delivery,
        )
        # poll(0) triggers pending delivery callbacks. Without it, callbacks
        # only fire at flush() time and error handling is delayed.
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> int:
        """Block until all queued messages are delivered (or timeout).
        Returns the number of messages still in the queue (0 = clean)."""
        return self._producer.flush(timeout)
