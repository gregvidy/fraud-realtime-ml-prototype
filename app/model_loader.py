"""
model_loader.py
---------------
Loads and caches the trained model, its preprocessor, and metadata.

Load priority for the scoring model:
  1. {model_stem}_calibrated.pkl  — CalibratedClassifierCV; used when calibration
     was enabled during training (best probability quality for decisioning)
  2. {model_stem}.pkl             — raw base model; used otherwise

The preprocessor artifact ({model_stem}_prep.pkl) is written by train_model.py.
It is None when the training config uses passthrough for all transformers, in
which case scoring operates on the raw feature vector as before.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)

_model: Any = None   # scoring model (calibrated if available, else raw)
_prep:  Any = None   # ColumnTransformer | None
_meta: dict = {}


def load_model() -> tuple[Any, dict]:
    global _model, _prep, _meta

    if _model is not None:
        return _model, _meta

    model_path = Path(os.getenv("MODEL_PATH", "models/fraud_model.pkl"))
    meta_path  = model_path.with_name("model_meta.json")
    prep_path  = model_path.with_name(f"{model_path.stem}_prep.pkl")
    calib_path = model_path.with_name(f"{model_path.stem}_calibrated.pkl")

    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found at {model_path}. Run 'make train' first."
        )

    if meta_path.exists():
        _meta = json.loads(meta_path.read_text())
    else:
        _meta = {
            "model_name":   "fraud_model",
            "threshold":    0.5,
            "feature_cols": [],
        }
        logger.warning("model_meta.json not found — using fallback metadata")

    # Load scoring model — prefer calibrated artifact when it exists
    if calib_path.exists():
        _model = joblib.load(calib_path)
        logger.info(
            "Calibrated model loaded from %s  (method=%s)",
            calib_path, _meta.get("calibration_method", "unknown"),
        )
    else:
        _model = joblib.load(model_path)
        logger.info("Base model loaded from %s", model_path)

    if prep_path.exists():
        _prep = joblib.load(prep_path)   # may be None (passthrough)
        if _prep is not None:
            logger.info("Preprocessor loaded from %s (%s)", prep_path, type(_prep).__name__)
    else:
        logger.info("No preprocessor artifact at %s — raw features used", prep_path)

    logger.info(
        "Model ready: %s  threshold=%.4f",
        _meta.get("model_name"), _meta.get("threshold", 0.5),
    )
    return _model, _meta


def get_model():
    return _model


def get_prep():
    """Return the fitted ColumnTransformer, or None if passthrough was used."""
    return _prep


def get_meta() -> dict:
    return _meta
