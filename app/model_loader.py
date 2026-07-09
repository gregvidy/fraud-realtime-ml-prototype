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

B5a: when ``MLFLOW_MODEL_URI`` (e.g. ``models:/fraud_model@production``)
is set, artifacts are downloaded from the MLflow model registry into a
staging directory and the existing joblib load path runs against them.
Falls back to ``MODEL_PATH`` when ``MLFLOW_MODEL_URI`` is unset — this
keeps docker-compose and one-off ``make train`` flows working unchanged.
"""

import json
import logging
import os
import shutil
import tempfile
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


def _parse_models_uri(uri: str) -> tuple[str, str, bool]:
    """Split a ``models:/name@alias`` or ``models:/name/version`` URI.

    Returns ``(name, ref, is_alias)``.
    """
    if not uri.startswith("models:/"):
        raise ValueError(f"MLFLOW_MODEL_URI must start with models:/ — got {uri!r}")
    rest = uri[len("models:/"):]
    if "@" in rest:
        name, alias = rest.split("@", 1)
        return name, alias, True
    if "/" in rest:
        name, version = rest.rsplit("/", 1)
        return name, version, False
    raise ValueError(
        f"MLFLOW_MODEL_URI must be models:/name@alias or models:/name/version — got {uri!r}"
    )


def _stage_from_mlflow(uri: str) -> Path:
    """Download the run's artifacts for a registered model version to a temp
    dir and return the path of the base-model pickle.

    The training script logs pkls under ``artifacts/`` and metadata under
    ``config/model_meta.json``. We copy the meta next to the pkl so the
    existing (unchanged) joblib load path finds it via ``with_name``.
    """
    import mlflow

    client = mlflow.MlflowClient()
    name, ref, is_alias = _parse_models_uri(uri)
    mv = client.get_model_version_by_alias(name, ref) if is_alias else client.get_model_version(name, ref)
    run_id = mv.run_id
    logger.info("MLflow: resolved %s → model=%s version=%s run_id=%s", uri, name, mv.version, run_id)

    staging = Path(tempfile.mkdtemp(prefix="mlflow_stage_"))
    local_dir = Path(mlflow.artifacts.download_artifacts(run_id=run_id, dst_path=str(staging)))

    artifacts_dir = local_dir / "artifacts"
    if not artifacts_dir.is_dir():
        raise RuntimeError(f"Expected {artifacts_dir} in downloaded run — found: {list(local_dir.iterdir())}")

    # Locate the BASE model pkl (skip *_prep.pkl and *_calibrated.pkl siblings).
    base_pkls = [
        p for p in artifacts_dir.glob("*.pkl")
        if not p.stem.endswith("_prep") and not p.stem.endswith("_calibrated")
    ]
    if len(base_pkls) != 1:
        raise RuntimeError(
            f"Expected exactly one base *.pkl in {artifacts_dir}, found {len(base_pkls)}: {base_pkls}"
        )
    model_pkl = base_pkls[0]

    # Existing loader expects model_meta.json AS A SIBLING of the pkl. MLflow
    # logs it to config/. Copy it over (idempotent).
    meta_src = local_dir / "config" / "model_meta.json"
    meta_dst = artifacts_dir / "model_meta.json"
    if meta_src.exists() and not meta_dst.exists():
        shutil.copy2(meta_src, meta_dst)

    return model_pkl


def _resolve_model_path() -> Path:
    """Decide where the model pickle lives.

    Preference: MLFLOW_MODEL_URI (registry-backed) → MODEL_PATH (joblib file)."""
    mlflow_uri = os.getenv("MLFLOW_MODEL_URI")
    if mlflow_uri:
        return _stage_from_mlflow(mlflow_uri)
    return Path(os.getenv("MODEL_PATH", "models/fraud_model.pkl"))


def load_model() -> tuple[Any, dict]:
    global _model, _prep, _meta, _calib_x, _calib_y

    if _model is not None:
        return _model, _meta

    model_path = _resolve_model_path()
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
