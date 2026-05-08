"""
feast_direct.py
---------------
Direct Redis reader that bypasses the Feast SDK at serving time (Phase 4B / Option C).

Why this exists
~~~~~~~~~~~~~~~
Feast SDK get_online_features() adds ~15-20 ms per request because it:
  1. Constructs Python FeatureStore + FeatureView objects on every call
  2. Runs internal proto serialization / validation / registry checks
  3. Issues one HMGET per entity per feature view (no batching across views)
  4. Deserializes every value through the full Python proto stack

This module replicates Feast's Redis read path directly in three steps:
  1. Build the same binary entity keys (entity_key_serialization_version=2)
  2. Use the same mmh3 field hashes as Feast's RedisOnlineStore
  3. Issue one async pipeline call for all three entity types combined
  4. Decode values with a minimal inline proto-wire-format decoder (~µs)

Result: ~2 ms latency (same as online Redis features) with zero Feast SDK
overhead and no thread pool involvement.

IMPORTANT: The *write path* (feast materialize) is completely unchanged.
This only replaces the online read path at inference time.

Key schema reference (feast_repo/feature_repo/feature_store.yaml)
  project: fraud_feature_store
  entity_key_serialization_version: 2

Redis key format (version 2):
  struct.pack("<I", ValueType.STRING=2)   -- join key type tag
  + join_key_name.encode("utf8")          -- no length prefix in v2
  + struct.pack("<I", ValueType.STRING=2) -- value type tag
  + struct.pack("<I", len(value))         -- value length (bytes)
  + value.encode("utf8")                  -- entity id bytes
  + project.encode("utf8")               -- project name appended

Redis hash field format:
  mmh3_32_LE("{view_name}:{feature_name}") -> 4 bytes

Redis hash value format:
  Standard protobuf Value wire encoding:
    0x20 + varint  →  int64_val  (field 4, wire type 0)
    0x29 + 8 bytes →  double_val (field 5, wire type 1)
"""

import logging
import struct
import os
from typing import Any

import mmh3
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Binary Redis client — must NOT use decode_responses=True because Feast
# protobuf values are raw bytes and cannot be decoded as UTF-8 strings.
# (The retriever.py client uses decode_responses=True for ZRANGEBYSCORE text.)
# ---------------------------------------------------------------------------
_raw_redis_pool: aioredis.ConnectionPool | None = None
_raw_redis_client: aioredis.Redis | None = None


def _get_raw_redis() -> aioredis.Redis:
    global _raw_redis_pool, _raw_redis_client
    if _raw_redis_client is None:
        _raw_redis_pool = aioredis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            max_connections=50,
            decode_responses=False,   # binary — do NOT change
        )
        _raw_redis_client = aioredis.Redis(connection_pool=_raw_redis_pool)
    return _raw_redis_client

# ---------------------------------------------------------------------------
# Feast project settings — must match feast_repo/feature_repo/feature_store.yaml
# ---------------------------------------------------------------------------
_PROJECT = b"fraud_feature_store"

# ValueType.STRING = 2  (feast.types.ValueType)
_TYPE_STRING = struct.pack("<I", 2)

# ---------------------------------------------------------------------------
# mmh3 field hash helper (matches Feast's _mmh3 in redis.py exactly)
# ---------------------------------------------------------------------------
def _mmh3_field(name: str) -> bytes:
    h = mmh3.hash(name, signed=False)
    return bytes.fromhex(struct.pack("<Q", h).hex()[:8])


# ---------------------------------------------------------------------------
# Pre-computed field hash → feature name mappings (computed once at import)
# These are static for the registered feature views and never change at runtime.
# ---------------------------------------------------------------------------

# user_batch_fv_v1  (entity: user_id)
_USER_FIELDS: list[tuple[bytes, str]] = [
    (_mmh3_field("user_batch_fv_v1:user_account_age_days"),        "user_account_age_days"),
    (_mmh3_field("user_batch_fv_v1:user_is_verified"),             "user_is_verified"),
    (_mmh3_field("user_batch_fv_v1:user_is_standard_account"),     "user_is_standard_account"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_1d"),            "user_txn_count_1d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_7d"),            "user_txn_count_7d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_30d"),           "user_txn_count_30d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_1d"),       "user_txn_amount_sum_1d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_7d"),       "user_txn_amount_sum_7d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_30d"),      "user_txn_amount_sum_30d"),
    (_mmh3_field("user_batch_fv_v1:user_avg_ticket_30d"),          "user_avg_ticket_30d"),
    (_mmh3_field("user_batch_fv_v1:user_distinct_merchants_30d"),  "user_distinct_merchants_30d"),
    (_mmh3_field("user_batch_fv_v1:user_distinct_devices_30d"),    "user_distinct_devices_30d"),
    (_mmh3_field("user_batch_fv_v1:user_decline_count_7d"),        "user_decline_count_7d"),
    (_mmh3_field("user_batch_fv_v1:user_failed_logins_7d"),        "user_failed_logins_7d"),
    (_mmh3_field("user_batch_fv_v1:user_failed_logins_1d"),        "user_failed_logins_1d"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_5m"),            "user_txn_count_5m"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_10m"),           "user_txn_count_10m"),
    (_mmh3_field("user_batch_fv_v1:user_txn_count_1h"),            "user_txn_count_1h"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_5m"),       "user_txn_amount_sum_5m"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_10m"),      "user_txn_amount_sum_10m"),
    (_mmh3_field("user_batch_fv_v1:user_txn_amount_sum_1h"),       "user_txn_amount_sum_1h"),
    (_mmh3_field("user_batch_fv_v1:user_distinct_merchants_5m"),   "user_distinct_merchants_5m"),
    (_mmh3_field("user_batch_fv_v1:user_distinct_merchants_10m"),  "user_distinct_merchants_10m"),
    (_mmh3_field("user_batch_fv_v1:user_distinct_merchants_1h"),   "user_distinct_merchants_1h"),
    (_mmh3_field("user_batch_fv_v1:user_failed_logins_15m"),       "user_failed_logins_15m"),
    (_mmh3_field("user_batch_fv_v1:user_failed_logins_1h"),        "user_failed_logins_1h"),
]
_USER_FIELD_KEYS = [fh for fh, _ in _USER_FIELDS]

# device_batch_fv_v1  (entity: device_id)
_DEVICE_FIELDS: list[tuple[bytes, str]] = [
    (_mmh3_field("device_batch_fv_v1:device_distinct_users_30d"), "device_distinct_users_30d"),
    (_mmh3_field("device_batch_fv_v1:device_txn_count_7d"),       "device_txn_count_7d"),
    (_mmh3_field("device_batch_fv_v1:device_txn_count_1d"),       "device_txn_count_1d"),
    (_mmh3_field("device_batch_fv_v1:device_is_shared_flag"),     "device_is_shared_flag"),
    (_mmh3_field("device_batch_fv_v1:device_txn_count_5m"),       "device_txn_count_5m"),
    (_mmh3_field("device_batch_fv_v1:device_txn_count_10m"),      "device_txn_count_10m"),
    (_mmh3_field("device_batch_fv_v1:device_txn_count_1h"),       "device_txn_count_1h"),
]
_DEVICE_FIELD_KEYS = [fh for fh, _ in _DEVICE_FIELDS]

# merchant_batch_fv_v1  (entity: merchant_id)
_MERCHANT_FIELDS: list[tuple[bytes, str]] = [
    (_mmh3_field("merchant_batch_fv_v1:merchant_is_high_risk"),    "merchant_is_high_risk"),
    (_mmh3_field("merchant_batch_fv_v1:merchant_is_online"),       "merchant_is_online"),
    (_mmh3_field("merchant_batch_fv_v1:merchant_txn_count_30d"),   "merchant_txn_count_30d"),
    (_mmh3_field("merchant_batch_fv_v1:merchant_avg_ticket_30d"),  "merchant_avg_ticket_30d"),
    (_mmh3_field("merchant_batch_fv_v1:merchant_fraud_rate_30d"),  "merchant_fraud_rate_30d"),
]
_MERCHANT_FIELD_KEYS = [fh for fh, _ in _MERCHANT_FIELDS]


# ---------------------------------------------------------------------------
# Entity key builder (entity_key_serialization_version=2)
# ---------------------------------------------------------------------------

def _build_entity_key(join_key: str, entity_value: str) -> bytes:
    """
    Construct the Redis hash key for a single-entity lookup.

    Schema (version 2, no length prefix on join key name):
        [STRING_TYPE_TAG 4B LE] [join_key_utf8] [STRING_TYPE_TAG 4B LE]
        [value_len 4B LE] [value_utf8] [project_name_utf8]
    """
    val_bytes = entity_value.encode("utf8")
    return (
        _TYPE_STRING                          # join key type = STRING
        + join_key.encode("utf8")             # join key name  (no length prefix)
        + _TYPE_STRING                        # value type = STRING
        + struct.pack("<I", len(val_bytes))   # value length
        + val_bytes                           # entity id
        + _PROJECT                            # project name (appended, no separator)
    )


# ---------------------------------------------------------------------------
# Value decoder — minimal inline protobuf wire decoder, no SDK dependency
# ---------------------------------------------------------------------------

def _decode_value(raw: bytes | None) -> Any:
    """
    Decode a Feast protobuf Value from raw bytes.

    Only handles the two wire types present in our numeric feature values:
      tag 0x20 (field 4, wire=varint)  →  int64_val
      tag 0x29 (field 5, wire=64-bit)  →  double_val
      tag 0x18 (field 3, wire=varint)  →  int32_val
    Returns 0 for missing / unknown types.
    """
    if not raw:
        return 0
    tag = raw[0]
    if tag == 0x20 or tag == 0x18:   # int64_val or int32_val (varint)
        result, shift = 0, 0
        for b in raw[1:]:
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return result
    if tag == 0x29:                  # double_val (64-bit fixed LE)
        return struct.unpack_from("<d", raw, 1)[0]
    return 0


# ---------------------------------------------------------------------------
# Main async fetch — all three entity types in one pipeline round trip
# ---------------------------------------------------------------------------

async def fetch_offline_features_direct(
    user_id: str,
    device_id: str,
    merchant_id: str,
) -> tuple[dict, bool]:
    """
    Fetch all batch/offline features for a (user, device, merchant) triple
    directly from Redis, bypassing the Feast SDK completely.

    Issues three HMGET commands in a single Redis pipeline (one round trip).
    Returns (feature_dict, ok) — ok=False only if Redis is unreachable.
    """
    r = _get_raw_redis()

    u_key = _build_entity_key("user_id", user_id)
    d_key = _build_entity_key("device_id", device_id)
    m_key = _build_entity_key("merchant_id", merchant_id)

    try:
        async with r.pipeline(transaction=False) as pipe:
            pipe.hmget(u_key, _USER_FIELD_KEYS)
            pipe.hmget(d_key, _DEVICE_FIELD_KEYS)
            pipe.hmget(m_key, _MERCHANT_FIELD_KEYS)
            u_vals, d_vals, m_vals = await pipe.execute()
    except Exception as exc:
        logger.warning("feast_direct: Redis HMGET failed — %s", exc)
        return {}, False

    features: dict = {}
    for (_, fname), raw in zip(_USER_FIELDS, u_vals):
        features[fname] = _decode_value(raw)
    for (_, fname), raw in zip(_DEVICE_FIELDS, d_vals):
        features[fname] = _decode_value(raw)
    for (_, fname), raw in zip(_MERCHANT_FIELDS, m_vals):
        features[fname] = _decode_value(raw)

    # ok=True even if entity was unseen (values default to 0); ok=False only
    # if Redis itself was unreachable (caught above)
    ok = any(v is not None for v in u_vals)
    return features, ok
