"""
feature_services.py — Feast feature service bundling all views for scoring.
"""

from feast import FeatureService

from .feature_views import device_batch_fv, merchant_batch_fv, user_batch_fv

fraud_scoring_v1 = FeatureService(
    name="fraud_scoring_v1",
    features=[
        user_batch_fv,
        device_batch_fv,
        merchant_batch_fv,
    ],
    description=(
        "Feature service for fraud scoring v1. "
        "Combines user, device, and merchant batch features."
    ),
    tags={"version": "1", "model": "fraud_xgb_v1"},
)
