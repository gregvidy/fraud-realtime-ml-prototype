"""
retriever.py
------------
Async Redis retriever — fetches all online sliding-window features in a
single pipeline round trip (Phase 3 optimisation).

All 11 ZRANGEBYSCORE commands are batched into one pipeline call, collapsing
6-8 sequential round trips into ~1 network call per request.
"""

import os
import time

import redis.asyncio as aioredis

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

_WINDOW_15M = 15 * 60

_redis_pool: aioredis.ConnectionPool | None = None
_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_pool, _redis_client
    if _redis_client is None:
        _redis_pool = aioredis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
            max_connections=100,
        )
        _redis_client = aioredis.Redis(connection_pool=_redis_pool)
    return _redis_client


async def get_all_online_features(
    user_id: str,
    device_id: str,
    now: float | None = None,
) -> dict:
    """
    Fetch all online sliding-window features in one Redis pipeline round trip.

    Issues 11 ZRANGEBYSCORE commands as a single pipelined batch, reducing
    network overhead from O(n_windows) to O(1) round trips per request.
    """
    r = _get_redis()
    now = now or time.time()

    cutoff_5m  = now - WINDOW_5M
    cutoff_10m = now - WINDOW_10M
    cutoff_1h  = now - WINDOW_1H
    cutoff_15m = now - _WINDOW_15M

    user_txn_key   = user_txn_zset(user_id)
    user_merch_key = user_merchant_zset(user_id)
    dev_txn_key    = device_txn_zset(device_id)
    login_key      = user_login_fail_zset(user_id)

    async with r.pipeline(transaction=False) as pipe:
        pipe.zrangebyscore(user_txn_key,   cutoff_5m,  "+inf")   # 0  user_txn 5m
        pipe.zrangebyscore(user_txn_key,   cutoff_10m, "+inf")   # 1  user_txn 10m
        pipe.zrangebyscore(user_txn_key,   cutoff_1h,  "+inf")   # 2  user_txn 1h
        pipe.zrangebyscore(user_merch_key, cutoff_5m,  "+inf")   # 3  user_merchant 5m
        pipe.zrangebyscore(user_merch_key, cutoff_10m, "+inf")   # 4  user_merchant 10m
        pipe.zrangebyscore(user_merch_key, cutoff_1h,  "+inf")   # 5  user_merchant 1h
        pipe.zrangebyscore(dev_txn_key,    cutoff_5m,  "+inf")   # 6  device_txn 5m
        pipe.zrangebyscore(dev_txn_key,    cutoff_10m, "+inf")   # 7  device_txn 10m
        pipe.zrangebyscore(dev_txn_key,    cutoff_1h,  "+inf")   # 8  device_txn 1h
        pipe.zrangebyscore(login_key,      cutoff_15m, "+inf")   # 9  login_fail 15m
        pipe.zrangebyscore(login_key,      cutoff_1h,  "+inf")   # 10 login_fail 1h
        results = await pipe.execute()

    txn_5m,   txn_10m,   txn_1h   = results[0], results[1], results[2]
    merch_5m, merch_10m, merch_1h = results[3], results[4], results[5]
    dev_5m,   dev_10m,   dev_1h   = results[6], results[7], results[8]
    login_15m, login_1h           = results[9], results[10]

    def _amount_sum(members: list) -> float:
        return sum(decode_txn_member(m)[1] for m in members if ":" in m)

    return {
        "user_txn_count_5m":           len(txn_5m),
        "user_txn_count_10m":          len(txn_10m),
        "user_txn_count_1h":           len(txn_1h),
        "user_txn_amount_sum_5m":      round(_amount_sum(txn_5m),   4),
        "user_txn_amount_sum_10m":     round(_amount_sum(txn_10m),  4),
        "user_txn_amount_sum_1h":      round(_amount_sum(txn_1h),   4),
        "user_distinct_merchants_5m":  len(set(merch_5m)),
        "user_distinct_merchants_10m": len(set(merch_10m)),
        "user_distinct_merchants_1h":  len(set(merch_1h)),
        "user_failed_logins_15m":      len(login_15m),
        "user_failed_logins_1h":       len(login_1h),
        "device_txn_count_5m":         len(dev_5m),
        "device_txn_count_10m":        len(dev_10m),
        "device_txn_count_1h":         len(dev_1h),
    }
