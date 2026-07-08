"""
streaming/consumers/base.py
---------------------------
`AsyncAvroConsumer` — reusable async Kafka consumer with Avro deserialisation.

Handles the boilerplate every consumer needs:
  - aiokafka connect + subscribe (topic list or regex pattern)
  - Schema-Registry-backed Avro decode of the value
  - Batching hook (yield windows of decoded records)
  - Manual offset commit (at-least-once semantics)
  - Progress logging + graceful shutdown

Concrete consumers just supply a `handler(records)` coroutine and a topic
pattern; base class runs the loop.
"""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aiokafka import AIOKafkaConsumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext


@dataclass
class DecodedRecord:
    """One deserialised Kafka record + enough metadata to commit / debug."""
    topic: str
    partition: int
    offset: int
    key: str | None
    value: dict


BatchHandler = Callable[[list[DecodedRecord]], Awaitable[None]]


class AsyncAvroConsumer:
    def __init__(
        self,
        *,
        name: str,
        group_id: str,
        bootstrap_servers: str,
        schema_registry_url: str,
        value_schema_path: Path,
        topics: list[str] | None = None,
        topic_pattern: str | None = None,
        batch_size: int = 500,
        batch_timeout_ms: int = 500,
        auto_offset_reset: str = "earliest",
    ) -> None:
        if bool(topics) == bool(topic_pattern):
            raise ValueError("Pass exactly one of `topics` or `topic_pattern`")

        self.name = name
        self.group_id = group_id
        self._topics = topics
        self._topic_pattern = topic_pattern
        self._batch_size = batch_size
        self._batch_timeout_ms = batch_timeout_ms

        # aiokafka: pass positional topics only when we have an explicit list.
        consumer_args: dict = {
            "bootstrap_servers": bootstrap_servers,
            "group_id": group_id,
            "enable_auto_commit": False,
            "auto_offset_reset": auto_offset_reset,
            # Fetch up to 5 MB per partition per request (~1000 events).
            "max_partition_fetch_bytes": 5 * 1024 * 1024,
        }
        if topics:
            self.consumer = AIOKafkaConsumer(*topics, **consumer_args)
        else:
            self.consumer = AIOKafkaConsumer(**consumer_args)

        sr = SchemaRegistryClient({"url": schema_registry_url})
        schema_str = Path(value_schema_path).read_text()
        self._avro_deser = AvroDeserializer(sr, schema_str)

        self._stop = asyncio.Event()
        self._processed = 0
        self._errors = 0
        self._started_at = 0.0

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop.set)

    def _decode(self, msg) -> DecodedRecord | None:
        try:
            value = self._avro_deser(
                msg.value,
                SerializationContext(msg.topic, MessageField.VALUE),
            )
        except Exception as exc:
            print(
                f"[{self.name}] DECODE ERROR at {msg.topic}[{msg.partition}]@{msg.offset}: {exc}"
            )
            self._errors += 1
            return None
        return DecodedRecord(
            topic=msg.topic,
            partition=msg.partition,
            offset=msg.offset,
            key=msg.key.decode() if msg.key else None,
            value=value,
        )

    async def run(self, handler: BatchHandler, *, duration: float = 0.0) -> None:
        await self.consumer.start()
        if self._topic_pattern:
            self.consumer.subscribe(pattern=self._topic_pattern)
        subscription = self._topic_pattern or ",".join(self._topics or [])
        self._install_signal_handlers()
        self._started_at = time.monotonic()

        print(f"[{self.name}] group={self.group_id} subscribed={subscription!r} "
              f"batch_size={self._batch_size} batch_timeout={self._batch_timeout_ms}ms")

        try:
            while not self._stop.is_set():
                if duration and (time.monotonic() - self._started_at) >= duration:
                    print(f"[{self.name}] duration={duration:.0f}s reached, stopping")
                    break

                # getmany() collects up to batch_size records per partition,
                # returns whatever arrives within batch_timeout_ms.
                result = await self.consumer.getmany(
                    timeout_ms=self._batch_timeout_ms,
                    max_records=self._batch_size,
                )
                if not result:
                    continue

                batch: list[DecodedRecord] = []
                for _tp, messages in result.items():
                    for msg in messages:
                        record = self._decode(msg)
                        if record is not None:
                            batch.append(record)

                if not batch:
                    continue

                try:
                    await handler(batch)
                except Exception as exc:
                    # Handler failure: don't commit — retry the batch on
                    # next loop. Print + count and keep going.
                    self._errors += 1
                    print(f"[{self.name}] HANDLER ERROR (n={len(batch)}): {exc!r}")
                    continue

                await self.consumer.commit()
                self._processed += len(batch)

                if (self._processed // 1000) > ((self._processed - len(batch)) // 1000):
                    elapsed = time.monotonic() - self._started_at
                    eps = self._processed / max(elapsed, 1e-9)
                    print(f"[{self.name}] processed={self._processed:,} "
                          f"errors={self._errors} elapsed={elapsed:.1f}s eps={eps:.1f}")
        finally:
            await self.consumer.stop()
            elapsed = time.monotonic() - self._started_at
            eps = self._processed / max(elapsed, 1e-9)
            print(f"[{self.name}] STOPPED  processed={self._processed:,} "
                  f"errors={self._errors} elapsed={elapsed:.1f}s avg_eps={eps:.1f}")
