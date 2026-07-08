"""
streaming/config.py — broker addresses, topic names, consumer groups.

Centralises every string that would otherwise be scattered across producer,
consumers, and infra scripts. Import from here.

The host- vs container-side broker distinction matters:
  - REDPANDA_BROKER_HOST     → producer/consumers run on the host (dev laptop)
  - REDPANDA_BROKER_INTERNAL → services running inside docker network

Redpanda's `--advertise-kafka-addr` is configured with both so a single broker
serves both clients.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Broker + registry ───────────────────────────────────────────────────────
REDPANDA_BROKER: str = os.getenv("REDPANDA_BROKER", "localhost:9092")
SCHEMA_REGISTRY_URL: str = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")

# ── Bank channels ───────────────────────────────────────────────────────────
CHANNELS: tuple[str, ...] = (
    "visa",
    "mastercard",
    "amex",
    "qris",       # Indonesia payment rail
    "debit",
    "digital",    # digital wallet / apps
)

# ── Topics ──────────────────────────────────────────────────────────────────
# Raw transaction events, one topic per channel. Partition key = user_id
# guarantees per-user ordering across the pipeline.
RAW_TXN_TOPICS: dict[str, str] = {ch: f"txn.raw.{ch}" for ch in CHANNELS}

TXN_SCORED_TOPIC: str = "txn.scored"
LOGIN_EVENTS_TOPIC: str = "login.events"

# ── Partitions & retention ──────────────────────────────────────────────────
# Per-topic tuning. Higher-volume channels get more partitions.
TOPIC_SPECS: dict[str, dict] = {
    # channel raw topics
    "txn.raw.visa":       {"partitions": 6, "retention_hours": 168},   # 7d
    "txn.raw.mastercard": {"partitions": 6, "retention_hours": 168},
    "txn.raw.amex":       {"partitions": 3, "retention_hours": 168},
    "txn.raw.qris":       {"partitions": 6, "retention_hours": 168},
    "txn.raw.debit":      {"partitions": 3, "retention_hours": 168},
    "txn.raw.digital":    {"partitions": 6, "retention_hours": 168},
    # scoring output — kept longer for monitoring/analysis
    TXN_SCORED_TOPIC:     {"partitions": 12, "retention_hours": 720}, # 30d
    # login events
    LOGIN_EVENTS_TOPIC:   {"partitions": 6, "retention_hours": 168},  # 7d
}

# ── Consumer groups (used by streaming/consumers/*) ─────────────────────────
GROUP_FRAUD_DECISIONING = "fraud-decisioning"
GROUP_FEATURE_STORE_UPDATER = "feature-store-updater"
GROUP_POSTGRES_SINK = "postgres-sink"

# ── Avro schema files ───────────────────────────────────────────────────────
_STREAMING_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = _STREAMING_DIR / "schemas"

TXN_EVENT_SCHEMA_PATH = SCHEMAS_DIR / "TxnEvent.avsc"
SCORED_TXN_EVENT_SCHEMA_PATH = SCHEMAS_DIR / "ScoredTxnEvent.avsc"
LOGIN_EVENT_SCHEMA_PATH = SCHEMAS_DIR / "LoginEvent.avsc"

# Schema Registry subject naming: `<topic>-value` is the Confluent default
# and Redpanda follows the same convention.
def value_subject(topic: str) -> str:
    return f"{topic}-value"
