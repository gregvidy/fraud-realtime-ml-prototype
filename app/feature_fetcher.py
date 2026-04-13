"""
feature_fetcher.py
------------------
Retrieves offline (Feast) and online (Redis) features for a given request.
Combines them into a flat feature dict keyed by feature name.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from .online_features.retriever import get_all_online_features

logger = logging.getLogger(__name__)

_feast_store = None


def _get_feast_store():
    global _feast_store
    if _feast_store is None:
        try:
            from feast import FeatureStore
            repo_path = os.getenv("FEAST_REPO_PATH", "feast_repo/feature_repo")
            _feast_store = FeatureStore(repo_path=repo_path)
            logger.info("Feast store loaded from %s", repo_path)
        except Exception as e:
            logger.warning("Feast store unavailable (%s) — offline features will use defaults", e)
            _feast_store = None
    return _feast_store


def fetch_offline_features(
    user_id: str,
    device_id: str,
    merchant_id: str,
) -> tuple[dict, bool]:
    """
    Retrieve offline features from Feast online store (previously materialized).
    Returns (feature_dict, feast_available).
    """
    store = _get_feast_store()
    if store is None:
        return {}, False

    try:
        entity_rows = [
            {"user_id": user_id, "device_id": device_id, "merchant_id": merchant_id}
        ]
        feature_vector = store.get_online_features(
            features=[
                "user_batch_fv:user_account_age_days",
                "user_batch_fv:user_is_verified",
                "user_batch_fv:user_is_standard_account",
                "user_batch_fv:user_txn_count_1d",
                "user_batch_fv:user_txn_count_7d",
                "user_batch_fv:user_txn_count_30d",
                "user_batch_fv:user_txn_amount_sum_1d",
                "user_batch_fv:user_txn_amount_sum_7d",
                "user_batch_fv:user_txn_amount_sum_30d",
                "user_batch_fv:user_avg_ticket_30d",
                "user_batch_fv:user_distinct_merchants_30d",
                "user_batch_fv:user_distinct_devices_30d",
                "user_batch_fv:user_decline_count_7d",
                "user_batch_fv:user_failed_logins_7d",
                "user_batch_fv:user_failed_logins_1d",
                "device_batch_fv:device_distinct_users_30d",
                "device_batch_fv:device_txn_count_7d",
                "device_batch_fv:device_txn_count_1d",
                "device_batch_fv:device_is_shared_flag",
                "merchant_batch_fv:merchant_is_high_risk",
                "merchant_batch_fv:merchant_is_online",
                "merchant_batch_fv:merchant_txn_count_30d",
                "merchant_batch_fv:merchant_avg_ticket_30d",
                "merchant_batch_fv:merchant_fraud_rate_30d",
                # Online features — used as fallback when Redis stream is not running
                "user_batch_fv:user_txn_count_5m",
                "user_batch_fv:user_txn_count_10m",
                "user_batch_fv:user_txn_count_1h",
                "user_batch_fv:user_txn_amount_sum_5m",
                "user_batch_fv:user_txn_amount_sum_10m",
                "user_batch_fv:user_txn_amount_sum_1h",
                "user_batch_fv:user_distinct_merchants_5m",
                "user_batch_fv:user_distinct_merchants_10m",
                "user_batch_fv:user_distinct_merchants_1h",
                "user_batch_fv:user_failed_logins_15m",
                "user_batch_fv:user_failed_logins_1h",
                "device_batch_fv:device_txn_count_5m",
                "device_batch_fv:device_txn_count_10m",
                "device_batch_fv:device_txn_count_1h",
            ],
            entity_rows=entity_rows,
        ).to_dict()

        # Flatten from list values to scalar (single entity row)
        features = {
            k: (v[0] if isinstance(v, list) and len(v) > 0 else v)
            for k, v in feature_vector.items()
            if k not in ("user_id", "device_id", "merchant_id")
        }
        return features, True

    except Exception as e:
        logger.warning("Feast feature retrieval failed: %s", e)
        return {}, False


def fetch_online_features(user_id: str, device_id: str) -> tuple[dict, bool]:
    """Retrieve sliding-window features from Redis."""
    try:
        features = get_all_online_features(user_id, device_id)
        return features, True
    except Exception as e:
        logger.warning("Redis feature retrieval failed: %s", e)
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
