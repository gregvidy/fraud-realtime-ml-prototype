"""
feature_fetcher.py
------------------
Retrieves offline (batch) and online (Redis) features for a given request.
Combines them into a flat feature dict keyed by feature name.

Optimisations applied
~~~~~~~~~~~~~~~~~~~~~
Phase 2:  fetch_offline_features and fetch_online_features are async.
Phase 3:  fetch_online_features delegates to the pipelined async retriever.
Phase 4A: Per-entity TTL caches (user / device / merchant) reduce redundant
          Redis reads.  Separate caches give ~80-90% hit rate on a fixed
          synthetic user pool vs ~0% with the old tripartite cache key.
Phase 4B: The Feast SDK is bypassed entirely at serving time.
          fetch_offline_features_direct() issues one async Redis pipeline
          call for all three entity types → ~2 ms instead of ~15-20 ms.
          The Feast SDK write path (feast materialize) is unchanged.
"""

import asyncio
import logging

from cachetools import TTLCache

from .clickhouse_fallback import get_fallback
from .feast_direct import fetch_offline_features_direct
from .online_features.retriever import get_all_online_features

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 4A: per-entity TTL caches
#
# Separate caches keyed by the entity's own ID collapse the key space:
#   old (user_id, device_id, merchant_id):  2000×4000×300 ≈ 2.4B  → ~0% hit
#   new per-entity at 300 RPS, 60s TTL:
#     user     2 000 entities, ~9 req/entity/window → ~89% hit rate
#     device   4 000 entities, ~4.5 req/entity/window → ~78% hit rate
#     merchant   300 entities, ~60 req/entity/window  → ~98% hit rate
# ---------------------------------------------------------------------------
_user_cache:     TTLCache = TTLCache(maxsize=10_000, ttl=60)
_device_cache:   TTLCache = TTLCache(maxsize=20_000, ttl=60)
_merchant_cache: TTLCache = TTLCache(maxsize=5_000,  ttl=60)

# Feature names that belong to each entity (used to split the combined dict
# for per-entity cache storage)
_USER_FEAT_NAMES: frozenset[str] = frozenset({
    "user_account_age_days", "user_is_verified", "user_is_standard_account",
    "user_txn_count_1d", "user_txn_count_7d", "user_txn_count_30d",
    "user_txn_amount_sum_1d", "user_txn_amount_sum_7d", "user_txn_amount_sum_30d",
    "user_avg_ticket_30d", "user_distinct_merchants_30d", "user_distinct_devices_30d",
    "user_decline_count_7d", "user_failed_logins_7d", "user_failed_logins_1d",
    "user_txn_count_5m", "user_txn_count_10m", "user_txn_count_1h",
    "user_txn_amount_sum_5m", "user_txn_amount_sum_10m", "user_txn_amount_sum_1h",
    "user_distinct_merchants_5m", "user_distinct_merchants_10m", "user_distinct_merchants_1h",
    "user_failed_logins_15m", "user_failed_logins_1h",
})
_DEVICE_FEAT_NAMES: frozenset[str] = frozenset({
    "device_distinct_users_30d", "device_txn_count_7d", "device_txn_count_1d",
    "device_is_shared_flag", "device_txn_count_5m", "device_txn_count_10m",
    "device_txn_count_1h",
})
_MERCHANT_FEAT_NAMES: frozenset[str] = frozenset({
    "merchant_is_high_risk", "merchant_is_online", "merchant_txn_count_30d",
    "merchant_avg_ticket_30d", "merchant_fraud_rate_30d",
})


async def fetch_offline_features(
    user_id: str,
    device_id: str,
    merchant_id: str,
) -> tuple[dict, bool]:
    """
    Retrieve batch/offline features via direct Redis reads (no Feast SDK).

    Cache strategy: each entity type is cached independently.
    All three caches must hit to skip the Redis call entirely.
    Any partial or full miss falls back to a single pipeline fetch.
    """
    u_cached = _user_cache.get(user_id)
    d_cached = _device_cache.get(device_id)
    m_cached = _merchant_cache.get(merchant_id)

    if u_cached is not None and d_cached is not None and m_cached is not None:
        features: dict = {}
        features.update(u_cached)
        features.update(d_cached)
        features.update(m_cached)
        return features, True

    # Partial or full cache miss → one pipeline fetch for all three entities.
    # Slice 10: hard-cap the Redis call at 500ms — if Redis is paused/broken
    # the pipeline await would otherwise hang forever. On timeout we fall
    # through to the ClickHouse cold-read path below.
    try:
        features, ok = await asyncio.wait_for(
            fetch_offline_features_direct(user_id, device_id, merchant_id),
            timeout=0.5,
        )
    except asyncio.TimeoutError:
        logger.warning("feature_fetcher: Redis fetch exceeded 500ms — treating as unavailable")
        features, ok = {}, False

    if ok and features:
        _user_cache[user_id]         = {k: v for k, v in features.items() if k in _USER_FEAT_NAMES}
        _device_cache[device_id]     = {k: v for k, v in features.items() if k in _DEVICE_FEAT_NAMES}
        _merchant_cache[merchant_id] = {k: v for k, v in features.items() if k in _MERCHANT_FEAT_NAMES}
        return features, ok

    # Slice 10: Redis unreachable → cold-read fallback from ClickHouse.
    # Returns a REDUCED feature set (last_txn_amount, last_txn_local_hour,
    # last_is_international). Missing model features default to 0 downstream.
    fallback_features, fallback_ok = await get_fallback().fetch_user_features(user_id)
    if fallback_ok and fallback_features:
        # Do NOT populate the per-entity TTL caches from fallback data — those
        # caches are sized for the Redis-hot path. Fallback is per-call.
        return fallback_features, False

    return features, ok


async def fetch_online_features(user_id: str, device_id: str) -> tuple[dict, bool]:
    """Retrieve sliding-window features from Redis (async, pipelined)."""
    try:
        features = await asyncio.wait_for(
            get_all_online_features(user_id, device_id), timeout=0.5
        )
        return features, True
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning("Redis online-feature retrieval failed: %s", e)
        return {}, False


def build_feature_vector(
    request_features: dict,
    feature_cols: list[str],
    offline_features: dict,
    online_features: dict,
) -> list[float]:
    """
    Assemble model input in the exact order defined by feature_cols.
    Missing features default to 0.
    """
    merged = {}
    merged.update(offline_features)
    merged.update(online_features)
    merged.update(request_features)

    vector = []
    for col in feature_cols:
        val = merged.get(col, 0)
        try:
            vector.append(float(val) if val is not None else 0.0)
        except (TypeError, ValueError):
            vector.append(0.0)
    return vector
