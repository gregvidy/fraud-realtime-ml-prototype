"""
scoring.py
----------
Core scoring logic: assembles features and produces a fraud score.

Phase 2 optimisation: score_transaction is now async.  Feast offline and
Redis online feature fetches run concurrently via asyncio.gather, saving
the full Redis fetch time (~5-10 ms) from the critical path compared to
the previous sequential calls.

Phase 6 optimisation: CalibratedClassifierCV predict_proba takes ~15-40 ms
(5 internal calibrators × sklearn validation overhead).  At high concurrency
this blocks the event loop, stalling all Redis I/O callbacks behind each
prediction.  Measured: feat wait inflated to 150-350 ms purely from event
loop starvation.

Fix: run predict_proba in a ThreadPoolExecutor.  This frees the event loop
for I/O while predictions run on OS threads.  LightGBM's C++ core releases
the GIL, allowing true parallelism for the inner predict call.
"""

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

from .feature_fetcher import build_feature_vector, fetch_offline_features, fetch_online_features
from .feature_logger import log_online_features
from .model_loader import get_calibration, get_meta, get_model, get_prep
from .schemas import ScoreRequest, ScoreResponse
from .score_logger import log_score

logger = logging.getLogger(__name__)

# Thread pool for CPU-bound predict_proba (CalibratedClassifierCV ~15-40ms).
# Sized per-worker: 8 threads gives 8 concurrent predictions per uvicorn worker.
_predict_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="predict")

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


def _predict(model, X: np.ndarray) -> float:
    """
    Run predict_proba in a thread — safe because LightGBM releases the GIL.
    If calibration arrays exist, apply fast np.interp instead of sklearn's
    CalibratedClassifierCV (which holds the GIL for 15-40ms).
    """
    raw_prob = float(model.predict_proba(X)[0, 1])
    calib_x, calib_y = get_calibration()
    if calib_x is not None:
        return float(np.interp(raw_prob, calib_x, calib_y))
    return raw_prob


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

    # --- Predict (thread pool — frees event loop for concurrent Redis I/O) ---
    X = np.array([vector], dtype=float)
    preprocessor = get_prep()
    if preprocessor is not None:
        X = preprocessor.transform(X)

    loop = asyncio.get_running_loop()
    score = await loop.run_in_executor(
        _predict_pool,
        functools.partial(_predict, model, X),
    )

    logger.debug(
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
