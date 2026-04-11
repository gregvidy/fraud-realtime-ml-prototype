"""
model_loader.py
---------------
Loads and caches the trained model and its metadata.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)

_model: Any = None
_meta: dict = {}


def load_model() -> tuple[Any, dict]:
    global _model, _meta

    if _model is not None:
        return _model, _meta

    model_path = Path(os.getenv("MODEL_PATH", "models/fraud_model.pkl"))
    meta_path  = model_path.with_name("model_meta.json")

    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found at {model_path}. Run 'make train' first."
        )

    logger.info("Loading model from %s", model_path)
    _model = joblib.load(model_path)

    if meta_path.exists():
        _meta = json.loads(meta_path.read_text())
    else:
        # Fallback metadata when meta file is missing
        _meta = {
            "model_name":   "fraud_xgb_v1",
            "threshold":    0.5,
            "feature_cols": [],
        }
        logger.warning("model_meta.json not found — using fallback metadata")

    logger.info("Model loaded: %s  threshold=%.4f", _meta.get("model_name"), _meta.get("threshold", 0.5))
    return _model, _meta


def get_model():
    return _model


def get_meta() -> dict:
    return _meta
