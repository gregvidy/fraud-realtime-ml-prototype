"""
retriever.py
------------
Retrieves online sliding-window features from Redis for a given entity.
Used by the FastAPI scoring service at inference time.
"""

import os
import time

import redis

from .redis_keys import (
    WINDOW_10M,
    WINDOW_1H,
    WINDOW_5M,
    decode_txn_member,
    device_txn_zset,
    user_login_fail_zset,
    user_merchant_zset,
    user_txn_zset,
)

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


def _zrange_window(r: redis.Redis, key: str, window_seconds: int, now: float) -> list[str]:
    """Return members whose score (timestamp) falls within [now - window, now]."""
    cutoff = now - window_seconds
    return r.zrangebyscore(key, cutoff, "+inf")


def get_user_online_features(user_id: str, now: float | None = None) -> dict:
    """
    Returns online features for a user:
      - user_txn_count_5m
      - user_txn_count_10m
      - user_txn_count_1h
      - user_txn_amount_sum_10m
      - user_distinct_merchants_1h
    """
    r   = _get_redis()
    now = now or time.time()

    txn_key    = user_txn_zset(user_id)
    merch_key  = user_merchant_zset(user_id)

    members_5m  = _zrange_window(r, txn_key, WINDOW_5M,  now)
    members_10m = _zrange_window(r, txn_key, WINDOW_10M, now)
    members_1h  = _zrange_window(r, txn_key, WINDOW_1H,  now)

    amount_sum_5m  = sum(decode_txn_member(m)[1] for m in members_5m  if ":" in m)
    amount_sum_10m = sum(decode_txn_member(m)[1] for m in members_10m if ":" in m)
    amount_sum_1h  = sum(decode_txn_member(m)[1] for m in members_1h  if ":" in m)

    merch_5m  = _zrange_window(r, merch_key, WINDOW_5M,  now)
    merch_10m = _zrange_window(r, merch_key, WINDOW_10M, now)
    merch_1h  = _zrange_window(r, merch_key, WINDOW_1H,  now)

    return {
        "user_txn_count_5m":           len(members_5m),
        "user_txn_count_10m":          len(members_10m),
        "user_txn_count_1h":           len(members_1h),
        "user_txn_amount_sum_5m":      round(amount_sum_5m,  4),
        "user_txn_amount_sum_10m":     round(amount_sum_10m, 4),
        "user_txn_amount_sum_1h":      round(amount_sum_1h,  4),
        "user_distinct_merchants_5m":  len(set(merch_5m)),
        "user_distinct_merchants_10m": len(set(merch_10m)),
        "user_distinct_merchants_1h":  len(set(merch_1h)),
    }


def get_device_online_features(device_id: str, now: float | None = None) -> dict:
    """
    Returns online features for a device:
      - device_txn_count_5m
      - device_txn_count_10m
    """
    r   = _get_redis()
    now = now or time.time()

    dev_key = device_txn_zset(device_id)

    members_5m  = _zrange_window(r, dev_key, WINDOW_5M,  now)
    members_10m = _zrange_window(r, dev_key, WINDOW_10M, now)
    members_1h  = _zrange_window(r, dev_key, WINDOW_1H,  now)

    return {
        "device_txn_count_5m":  len(members_5m),
        "device_txn_count_10m": len(members_10m),
        "device_txn_count_1h":  len(members_1h),
    }


def get_user_login_features(user_id: str, now: float | None = None) -> dict:
    """
    Returns online login failure features for a user:
      - user_failed_logins_15m
    """
    r   = _get_redis()
    now = now or time.time()

    key         = user_login_fail_zset(user_id)
    members_15m = _zrange_window(r, key, 15 * 60,  now)
    members_1h  = _zrange_window(r, key, WINDOW_1H, now)

    return {
        "user_failed_logins_15m": len(members_15m),
        "user_failed_logins_1h":  len(members_1h),
    }


def get_all_online_features(
    user_id: str,
    device_id: str,
    now: float | None = None,
) -> dict:
    """Convenience wrapper — returns merged online feature dict."""
    now = now or time.time()
    features: dict = {}
    features.update(get_user_online_features(user_id, now))
    features.update(get_device_online_features(device_id, now))
    features.update(get_user_login_features(user_id, now))
    return features
