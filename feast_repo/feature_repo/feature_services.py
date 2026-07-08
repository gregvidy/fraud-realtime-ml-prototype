"""
feature_services.py — Feast feature service bundling all views for scoring.

Feature service version (fraud_scoring_v1) is pinned to the v1 set of
feature views.  When any feature view introduces a breaking logic change
(new version suffix), create a new FeatureService (fraud_scoring_v2) and
update the FastAPI service to consume it before deprecating v1.
"""

from feast import FeatureService

from feature_views import device_batch_fv_v1, merchant_batch_fv_v1, user_batch_fv_v1

fraud_scoring_v1 = FeatureService(
    name="fraud_scoring_v1",
    features=[
        user_batch_fv_v1,
        device_batch_fv_v1,
        merchant_batch_fv_v1,
    ],
    description=(
        "Feature service for fraud scoring v1. "
        "Combines user, device, and merchant batch features sourced from ClickHouse via dbt."
    ),
    tags={"version": "1", "model": "fraud_xgb_v1", "offline_store": "clickhouse"},
)
