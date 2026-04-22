"""
scoring.py
----------
Core scoring logic: assembles features and produces a fraud score.

Phase 2 optimisation: score_transaction is now async.  Feast offline and
Redis online feature fetches run concurrently via asyncio.gather, saving
the full Redis fetch time (~5-10 ms) from the critical path compared to
the previous sequential calls.
"""

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np

from .feature_fetcher import build_feature_vector, fetch_offline_features, fetch_online_features
from .feature_logger import log_online_features
from .model_loader import get_meta, get_model, get_prep
from .schemas import ScoreRequest, ScoreResponse
from .score_logger import log_score

logger = logging.getLogger(__name__)

_RISK_BANDS = [
    (0.80, "critical"),
    (0.50, "high"),
    (0.20, "medium"),
    (0.00, "low"),
]

# Feature service version consumed by this scorer.  Update when switching to
# a new Feast FeatureService (e.g. fraud_scoring_v2).
FEATURE_SERVICE_VERSION = "fraud_scoring_v1"


def _risk_band(score: float) -> str:
    for threshold, band in _RISK_BANDS:
        if score >= threshold:
            return band
    return "low"


async def score_transaction(request: ScoreRequest) -> ScoreResponse:
    model = get_model()
    meta  = get_meta()

    if model is None:
        raise RuntimeError("Model not loaded. Call load_model() at startup.")

    feature_cols = meta.get("feature_cols", [])
    threshold    = meta.get("threshold", 0.5)
    model_name   = meta.get("model_name", "unknown")

    # --- Request-time features ---
    now_hour = datetime.now(timezone.utc).hour
    request_features = {
        "txn_amount":       request.amount,
        "is_international": int(request.is_international),
        "local_hour":       request.local_hour if request.local_hour is not None else now_hour,
    }

    # --- Parallel feature fetches (Phase 2) ---
    (offline_feats, feast_ok), (online_feats, redis_ok) = await asyncio.gather(
        fetch_offline_features(request.user_id, request.device_id, request.merchant_id),
        fetch_online_features(request.user_id, request.device_id),
    )

    # --- Log online features for training-serving consistency (non-blocking) ---
    if redis_ok:
        log_online_features(
            transaction_id=request.transaction_id,
            user_id=request.user_id,
            device_id=request.device_id,
            online_features=online_feats,
        )

    # --- Assemble vector ---
    vector = build_feature_vector(
        request_features, feature_cols, offline_feats, online_feats
    )

    # --- Predict ---
    X = np.array([vector], dtype=float)
    preprocessor = get_prep()
    if preprocessor is not None:
        X = preprocessor.transform(X)
    score = float(model.predict_proba(X)[0, 1])

    logger.info(
        "score  txn=%s  user=%s  score=%.4f  feast=%s  redis=%s",
        request.transaction_id, request.user_id, score, feast_ok, redis_ok,
    )

    # --- Log score (non-blocking enqueue) ---
    log_score(
        transaction_id           = request.transaction_id,
        user_id                  = request.user_id,
        device_id                = request.device_id,
        merchant_id              = request.merchant_id,
        fraud_score              = score,
        risk_band                = _risk_band(score),
        is_flagged               = score >= threshold,
        model_version            = model_name,
        feature_service_version  = FEATURE_SERVICE_VERSION,
        feast_offline_ok         = feast_ok,
        redis_online_ok          = redis_ok,
    )

    return ScoreResponse(
        transaction_id=request.transaction_id,
        score=round(score, 6),
        risk_band=_risk_band(score),
        is_flagged=score >= threshold,
        model_version=model_name,
        feature_sources={
            "feast_offline": feast_ok,
            "redis_online":  redis_ok,
            "request_time":  True,
        },
    )
