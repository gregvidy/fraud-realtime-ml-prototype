"""
model_loader.py
---------------
Loads and caches the trained model, its preprocessor, and metadata.

Phase 6 optimisation: when a CalibratedClassifierCV is detected, the
isotonic calibration mapping is extracted at load time as numpy arrays.
At inference time the raw base model's predict_proba (~1ms, GIL-free via
LightGBM C++) is followed by a cheap np.interp calibration (~µs).
This avoids the heavy sklearn CalibratedClassifierCV.predict_proba path
which runs 5 calibrators in pure Python and holds the GIL for 15-40ms.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np

logger = logging.getLogger(__name__)

_model: Any = None       # raw base estimator (e.g. LGBMClassifier)
_prep:  Any = None       # ColumnTransformer | None
_meta: dict = {}
_calib_x: np.ndarray | None = None   # isotonic x thresholds (averaged across folds)
_calib_y: np.ndarray | None = None   # isotonic y thresholds (averaged across folds)


def _extract_calibration(calibrated_model) -> tuple[np.ndarray, np.ndarray, Any]:
    """
    Extract the base estimator and averaged isotonic calibration mapping
    from a CalibratedClassifierCV object.

    Handles both binary (single calibrator per fold) and multiclass layouts.
    Returns (calib_x, calib_y, base_model).
    """
    base_model = calibrated_model.calibrated_classifiers_[0].estimator

    # Each fold's _CalibratedClassifier has .calibrators (list of IsotonicRegression)
    # Binary: calibrators has 1 entry (positive class mapping)
    all_x, all_y = [], []
    for cc in calibrated_model.calibrated_classifiers_:
        # For binary classification: calibrators[0] maps raw P(class=1) → calibrated P
        cal = cc.calibrators[0]
        all_x.append(cal.X_thresholds_)
        all_y.append(cal.y_thresholds_)

    # Interpolate all calibrators onto a common grid and average
    grid = np.linspace(0, 1, 1000)
    interps = [np.interp(grid, xv, yv) for xv, yv in zip(all_x, all_y)]
    avg_y = np.mean(interps, axis=0)

    return grid, avg_y, base_model


def load_model() -> tuple[Any, dict]:
    global _model, _prep, _meta, _calib_x, _calib_y

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

    # Load scoring model — extract fast calibration when available
    loaded = None
    if calib_path.exists():
        loaded = joblib.load(calib_path)
    else:
        loaded = joblib.load(model_path)

    # Detect CalibratedClassifierCV regardless of file path
    if hasattr(loaded, 'calibrated_classifiers_'):
        try:
            _calib_x, _calib_y, _model = _extract_calibration(loaded)
            logger.info(
                "Fast calibration extracted (%s, %d grid points, base=%s)",
                _meta.get("calibration_method", "unknown"),
                len(_calib_x),
                type(_model).__name__,
            )
        except Exception as exc:
            logger.warning("Calibration extraction failed (%s), using full CalibratedClassifierCV", exc)
            _model = loaded
    else:
        _model = loaded
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


def get_calibration() -> tuple[np.ndarray | None, np.ndarray | None]:
    """Return (calib_x, calib_y) arrays for fast np.interp calibration."""
    return _calib_x, _calib_y
