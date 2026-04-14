"""
feature_views.py — Feast feature view definitions for fraud detection.

Versioning convention: <entity>_batch_fv_v<N>
  - Increment N on any breaking change to feature logic or schema.
  - Non-breaking additions (new fields) do not require a new version.
  - Old versions are kept registered until all consumers have migrated.

Current versions:
  user_batch_fv_v1     — initial DuckDB-backed offline feature set
  device_batch_fv_v1   — initial DuckDB-backed offline feature set
  merchant_batch_fv_v1 — initial DuckDB-backed offline feature set
"""

from datetime import timedelta

from feast import FeatureView, Field
from feast.types import Float64, Int64

from entities import device, merchant, user
from data_sources import (
    device_features_source,
    merchant_features_source,
    user_features_source,
)

# ---------------------------------------------------------------------------
# User batch feature view — v1
# ---------------------------------------------------------------------------
user_batch_fv_v1 = FeatureView(
    name="user_batch_fv_v1",
    entities=[user],
    ttl=timedelta(days=3),
    schema=[
        Field(name="user_account_age_days",        dtype=Int64),
        Field(name="user_is_verified",             dtype=Int64),
        Field(name="user_is_standard_account",     dtype=Int64),
        Field(name="user_txn_count_1d",            dtype=Int64),
        Field(name="user_txn_count_7d",            dtype=Int64),
        Field(name="user_txn_count_30d",           dtype=Int64),
        Field(name="user_txn_amount_sum_1d",       dtype=Float64),
        Field(name="user_txn_amount_sum_7d",       dtype=Float64),
        Field(name="user_txn_amount_sum_30d",      dtype=Float64),
        Field(name="user_avg_ticket_30d",          dtype=Float64),
        Field(name="user_distinct_merchants_30d",  dtype=Int64),
        Field(name="user_distinct_devices_30d",    dtype=Int64),
        Field(name="user_decline_count_7d",        dtype=Int64),
        Field(name="user_failed_logins_7d",        dtype=Int64),
        Field(name="user_failed_logins_1d",        dtype=Int64),
        # Online / micro-batch features (mirrors Redis sliding-window)
        Field(name="user_txn_count_5m",            dtype=Int64),
        Field(name="user_txn_count_10m",           dtype=Int64),
        Field(name="user_txn_count_1h",            dtype=Int64),
        Field(name="user_txn_amount_sum_5m",       dtype=Float64),
        Field(name="user_txn_amount_sum_10m",      dtype=Float64),
        Field(name="user_txn_amount_sum_1h",       dtype=Float64),
        Field(name="user_distinct_merchants_5m",   dtype=Int64),
        Field(name="user_distinct_merchants_10m",  dtype=Int64),
        Field(name="user_distinct_merchants_1h",   dtype=Int64),
        Field(name="user_failed_logins_15m",       dtype=Int64),
        Field(name="user_failed_logins_1h",        dtype=Int64),
    ],
    source=user_features_source,
    description="v1: Rolling window user-level features from dbt + DuckDB offline pipeline.",
    tags={"owner": "model_team", "pipeline": "dbt_duckdb", "version": "1"},
)

# ---------------------------------------------------------------------------
# Device batch feature view — v1
# ---------------------------------------------------------------------------
device_batch_fv_v1 = FeatureView(
    name="device_batch_fv_v1",
    entities=[device],
    ttl=timedelta(days=3),
    schema=[
        Field(name="device_distinct_users_30d", dtype=Int64),
        Field(name="device_txn_count_7d",       dtype=Int64),
        Field(name="device_txn_count_1d",       dtype=Int64),
        Field(name="device_is_shared_flag",     dtype=Int64),
        # Online / micro-batch features (mirrors Redis sliding-window)
        Field(name="device_txn_count_5m",       dtype=Int64),
        Field(name="device_txn_count_10m",      dtype=Int64),
        Field(name="device_txn_count_1h",       dtype=Int64),
    ],
    source=device_features_source,
    description="v1: Rolling window device-level features from dbt + DuckDB offline pipeline.",
    tags={"owner": "model_team", "pipeline": "dbt_duckdb", "version": "1"},
)

# ---------------------------------------------------------------------------
# Merchant batch feature view — v1
# ---------------------------------------------------------------------------
merchant_batch_fv_v1 = FeatureView(
    name="merchant_batch_fv_v1",
    entities=[merchant],
    ttl=timedelta(days=3),
    schema=[
        Field(name="merchant_is_high_risk",      dtype=Int64),
        Field(name="merchant_is_online",         dtype=Int64),
        Field(name="merchant_txn_count_30d",     dtype=Int64),
        Field(name="merchant_avg_ticket_30d",    dtype=Float64),
        Field(name="merchant_fraud_rate_30d",    dtype=Float64),
    ],
    source=merchant_features_source,
    description="v1: Rolling window merchant-level features from dbt + DuckDB offline pipeline.",
    tags={"owner": "model_team", "pipeline": "dbt_duckdb", "version": "1"},
)
