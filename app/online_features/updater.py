"""
updater.py
----------
Consumes a transaction event dict and updates Redis online features.
Called by stream_transactions.py for each new event.

Each call:
  1. Adds the event to per-user and per-device sorted sets (score = unix timestamp)
  2. Trims expired members outside the longest window (1h) to bound memory
  3. Refreshes TTL on touched keys
"""

import os
import time
from datetime import datetime, timezone

import redis

from .redis_keys import (
    TTL_ZSET,
    WINDOW_1H,
    device_txn_zset,
    encode_txn_member,
    user_login_fail_zset,
    user_merchant_zset,
    user_txn_zset,
)

# ---------------------------------------------------------------------------
# Redis client (module-level singleton)
# ---------------------------------------------------------------------------
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
        )
    return _redis_client


# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------

def update_online_features(event: dict) -> None:
    """
    Update Redis sliding-window features from a transaction event.

    Expected event keys:
        transaction_id, user_id, device_id, merchant_id, amount,
        txn_status, event_timestamp (ISO8601)
    """
    r = _get_redis()

    user_id       = event["user_id"]
    device_id     = event["device_id"]
    merchant_id   = event["merchant_id"]
    txn_id        = event["transaction_id"]
    amount        = float(event.get("amount", 0))

    # Parse event timestamp → unix score
    raw_ts = event.get("event_timestamp")
    if raw_ts is None:
        ts_unix = time.time()
    elif isinstance(raw_ts, (int, float)):
        ts_unix = float(raw_ts)
    else:
        dt = datetime.fromisoformat(raw_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_unix = dt.timestamp()

    cutoff = ts_unix - WINDOW_1H  # trim anything older than 1h

    pipe = r.pipeline(transaction=False)

    # --- User transaction sorted set ---
    user_key   = user_txn_zset(user_id)
    txn_member = encode_txn_member(txn_id, amount)
    pipe.zadd(user_key, {txn_member: ts_unix})
    pipe.zremrangebyscore(user_key, "-inf", cutoff)
    pipe.expire(user_key, TTL_ZSET)

    # --- User distinct merchants sorted set ---
    merch_key = user_merchant_zset(user_id)
    pipe.zadd(merch_key, {merchant_id: ts_unix})
    pipe.zremrangebyscore(merch_key, "-inf", cutoff)
    pipe.expire(merch_key, TTL_ZSET)

    # --- Device transaction sorted set ---
    dev_key = device_txn_zset(device_id)
    pipe.zadd(dev_key, {txn_member: ts_unix})
    pipe.zremrangebyscore(dev_key, "-inf", cutoff)
    pipe.expire(dev_key, TTL_ZSET)

    pipe.execute()


def update_login_failure(user_id: str, event_timestamp: str | float | None = None) -> None:
    """Record a failed login event for a user."""
    r = _get_redis()

    ts_unix = (
        float(event_timestamp)
        if isinstance(event_timestamp, (int, float))
        else (
            datetime.fromisoformat(event_timestamp).replace(tzinfo=timezone.utc).timestamp()
            if event_timestamp
            else time.time()
        )
    )

    cutoff = ts_unix - WINDOW_1H
    key    = user_login_fail_zset(user_id)
    member = f"login:{ts_unix}"

    pipe = r.pipeline(transaction=False)
    pipe.zadd(key, {member: ts_unix})
    pipe.zremrangebyscore(key, "-inf", cutoff)
    pipe.expire(key, TTL_ZSET)
    pipe.execute()
