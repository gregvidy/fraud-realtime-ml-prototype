"""
train_model.py
--------------
Config-driven fraud detection training pipeline.

Supports:
  - Temporal (OOT) or random train/val split              (config: split)
  - Models   : XGBoost | LightGBM | RandomForest          (config: model.type)
  - Preprocessing : passthrough | standard_scaler |        (config: preprocessing)
                    minmax_scaler | robust_scaler (numeric)
                    passthrough | ordinal | one_hot (categorical)
  - Calibration   : none | sigmoid | isotonic | beta       (config: calibration)

Configuration lives entirely in training_config.yaml — no code changes needed
for experiments.

Usage:
    python training/train_model.py
    python training/train_model.py --config path/to/other_config.yaml

Outputs:
  models/{output_name}.pkl            — base (uncalibrated) model artifact
  models/{output_name}_prep.pkl       — fitted ColumnTransformer (None if passthrough)
  models/{output_name}_calibrated.pkl — CalibratedClassifierCV (absent if calibration=none)
  models/model_meta.json              — metadata used by evaluate_model.py and scoring service
"""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv
import mlflow
import mlflow.sklearn
from sklearn.compose import ColumnTransformer
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.preprocessing import (
    MinMaxScaler,
    OneHotEncoder,
    OrdinalEncoder,
    RobustScaler,
    StandardScaler,
)

load_dotenv()

_TRAINING_DIR  = Path(__file__).parent
_PROJECT_ROOT  = _TRAINING_DIR.parent
DEFAULT_CONFIG = _TRAINING_DIR / "training_config.yaml"

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Split helpers
# ---------------------------------------------------------------------------

def _temporal_split(
    df: pd.DataFrame, ts_col: str, cfg: dict
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Sort by event_timestamp; OOT = most-recent (1 - cutoff_quantile) slice."""
    df = df.sort_values(ts_col).reset_index(drop=True)
    cutoff_date = cfg.get("cutoff_date")
    if cutoff_date:
        cutoff = pd.Timestamp(cutoff_date)
    else:
        quantile = float(cfg.get("cutoff_quantile", 0.80))
        n = len(df)
        cutoff = df[ts_col].iloc[int(n * quantile)]

    train_df = df[df[ts_col] < cutoff]
    val_df   = df[df[ts_col] >= cutoff]
    cutoff_str = str(cutoff)
    print(f"Temporal split cutoff : {cutoff_str}")
    print(f"  Train : {len(train_df):>8,} rows")
    print(f"  OOT   : {len(val_df):>8,} rows")
    return train_df, val_df, cutoff_str


def _random_split(
    df: pd.DataFrame, label_col: str, cfg: dict
) -> tuple[pd.DataFrame, pd.DataFrame, None]:
    """Stratified random split."""
    from sklearn.model_selection import train_test_split
    test_size = float(cfg.get("random_test_size", 0.20))
    seed      = int(cfg.get("random_seed", 42))
    train_df, val_df = train_test_split(
        df, test_size=test_size, stratify=df[label_col], random_state=seed
    )
    print(f"Random stratified split : train={len(train_df):,}  val={len(val_df):,}")
    return train_df, val_df, None

# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

_NUMERIC_TRANSFORMERS: dict = {
    "passthrough":    "passthrough",
    "standard_scaler": StandardScaler(),
    "minmax_scaler":   MinMaxScaler(),
    "robust_scaler":   RobustScaler(),
}

_CAT_TRANSFORMERS: dict = {
    "passthrough": "passthrough",
    "ordinal":     OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
    "one_hot":     OneHotEncoder(handle_unknown="ignore", sparse_output=False),
}


def _build_preprocessor(
    numeric_cols: list[str],
    cat_cols: list[str],
    cfg: dict,
) -> ColumnTransformer | None:
    """Return a ColumnTransformer, or None when everything is passthrough."""
    num_key = cfg.get("numeric_transformer", "passthrough")
    cat_key = cfg.get("categorical_transformer", "ordinal")

    num_tr = _NUMERIC_TRANSFORMERS.get(num_key, "passthrough")
    cat_tr = _CAT_TRANSFORMERS.get(cat_key, "passthrough")

    transformers = []
    if numeric_cols:
        transformers.append(("num", num_tr, numeric_cols))
    if cat_cols:
        transformers.append(("cat", cat_tr, cat_cols))

    if not transformers or all(t[1] == "passthrough" for t in transformers):
        return None         # no-op — skip serialising an identity transformer

    return ColumnTransformer(transformers, remainder="drop")

# ---------------------------------------------------------------------------
# Model builder
# ---------------------------------------------------------------------------

def _build_model(cfg: dict, pos_weight: float) -> tuple:
    """Return (model, early_stopping_rounds_or_None)."""
    model_type = cfg["type"]

    if model_type == "xgboost":
        from xgboost import XGBClassifier
        params = dict(cfg.get("xgboost", {}))
        params["scale_pos_weight"] = pos_weight
        # early_stopping_rounds is a constructor param in XGBoost >= 2
        return XGBClassifier(**params), None

    if model_type == "lightgbm":
        from lightgbm import LGBMClassifier
        params = dict(cfg.get("lightgbm", {}))
        params["scale_pos_weight"] = pos_weight
        es_rounds = params.pop("early_stopping_rounds", 30)
        return LGBMClassifier(**params), es_rounds

    if model_type == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        params = dict(cfg.get("random_forest", {}))
        params["class_weight"] = "balanced"
        return RandomForestClassifier(**params), None

    raise ValueError(
        f"Unknown model type: '{model_type}'. "
        "Valid options: xgboost | lightgbm | random_forest"
    )

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _carve_calibration_set(
    train_df: pd.DataFrame,
    label_col: str,
    calib_fraction: float,
    calib_seed: int,
    split_method: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split training data into base-train and calibration subsets.

    For temporal splits the calibration set is the most-recent tail of the
    training data (closer in time to the OOT boundary, so it is representative
    of the distribution the calibrator will score at inference).
    For random splits a stratified sample is used.
    """
    n_calib = max(1, int(len(train_df) * calib_fraction))
    if split_method == "temporal":
        # train_df is already sorted ascending by timestamp
        base_df  = train_df.iloc[:-n_calib].copy()
        calib_df = train_df.iloc[-n_calib:].copy()
    else:
        from sklearn.model_selection import train_test_split
        base_df, calib_df = train_test_split(
            train_df,
            test_size=calib_fraction,
            stratify=train_df[label_col],
            random_state=calib_seed,
        )
    print(
        f"Calibration set carved : base_train={len(base_df):,}  calib={len(calib_df):,}"
        f"  fraud_calib={calib_df[label_col].sum():,}"
    )
    return base_df, calib_df


def _fit_calibrator(model, X_calib: np.ndarray, y_calib: np.ndarray, method: str):
    """
    Fit and return a CalibratedClassifierCV wrapping the already-trained model.
    Uses cv='prefit' so the base model is never refitted.

    method: 'sigmoid' | 'isotonic' | 'beta'
    """
    if method == "beta":
        try:
            from betacal import BetaCalibration  # type: ignore
            from sklearn.calibration import CalibratedClassifierCV

            class _BetaWrapper:
                """Thin sklearn-compatible wrapper around betacal."""
                def __init__(self, base):
                    self._base = base
                    self._bc   = BetaCalibration()

                def fit(self, X, y):          # called by CalibratedClassifierCV
                    raw = self._base.predict_proba(X)[:, 1]
                    self._bc.fit(raw.reshape(-1, 1), y)
                    return self

                def predict_proba(self, X):
                    raw = self._base.predict_proba(X)[:, 1]
                    cal = self._bc.predict(raw.reshape(-1, 1))
                    return np.column_stack([1 - cal, cal])

                # passthrough attributes sklearn may probe
                def __getattr__(self, name):
                    return getattr(self._base, name)

            wrapped = _BetaWrapper(model)
            wrapped.fit(X_calib, y_calib)
            print("  Beta calibration fitted (betacal).")
            return wrapped
        except ImportError:
            print("  betacal not installed — falling back to isotonic calibration.")
            method = "isotonic"

    from sklearn.calibration import CalibratedClassifierCV
    calibrated = CalibratedClassifierCV(model, cv="prefit", method=method)
    calibrated.fit(X_calib, y_calib)
    print(f"  {method.capitalize()} calibration fitted ({len(y_calib):,} rows).")
    return calibrated


# ---------------------------------------------------------------------------
# Threshold selection
# ---------------------------------------------------------------------------

def _choose_threshold(y_val: np.ndarray, proba: np.ndarray, cfg: dict) -> float:
    strategy = cfg.get("strategy", "recall_target")
    if strategy == "fixed":
        return float(cfg.get("fixed_value", 0.5))
    target_recall = float(cfg.get("target_recall", 0.80))
    precision, recall, thresholds = precision_recall_curve(y_val, proba)
    valid = np.where(recall[:-1] >= target_recall)[0]
    if len(valid):
        best = valid[np.argmax(precision[valid])]
        return float(thresholds[best])
    return 0.5


# ---------------------------------------------------------------------------
# MLflow helpers
# ---------------------------------------------------------------------------

def _collect_mlflow_params(
    cfg: dict,
    model_type: str,
    numeric_cols: list[str],
    cat_cols: list[str],
    pos_weight: float,
) -> dict[str, str]:
    """Flatten experiment config into a flat str→str dict for mlflow.log_params()."""
    params: dict[str, str] = {}

    split_cfg = cfg["split"]
    params["split.method"]           = str(split_cfg.get("method", "temporal"))
    params["split.cutoff_quantile"]  = str(split_cfg.get("cutoff_quantile", 0.80))
    params["split.cutoff_date"]      = str(split_cfg.get("cutoff_date") or "")
    params["split.random_test_size"] = str(split_cfg.get("random_test_size", 0.20))
    params["split.random_seed"]      = str(split_cfg.get("random_seed", 42))

    prep_cfg = cfg["preprocessing"]
    params["preprocessing.numeric_transformer"]     = prep_cfg.get("numeric_transformer", "passthrough")
    params["preprocessing.categorical_transformer"] = prep_cfg.get("categorical_transformer", "passthrough")

    params["features.n_numeric"]     = str(len(numeric_cols))
    params["features.n_categorical"] = str(len(cat_cols))
    params["features.n_total"]       = str(len(numeric_cols) + len(cat_cols))
    # Full feature list as artifact; truncated param for quick filtering in UI
    feat_str = ",".join(numeric_cols + cat_cols)
    params["features.list"] = feat_str[:490]

    params["model.type"]       = model_type
    params["model.pos_weight"] = str(round(pos_weight, 4))
    for k, v in cfg["model"].get(model_type, {}).items():
        params[f"model.{k}"] = str(v)

    thresh_cfg = cfg["threshold"]
    params["threshold.strategy"]      = thresh_cfg.get("strategy", "recall_target")
    params["threshold.target_recall"] = str(thresh_cfg.get("target_recall", 0.80))
    params["threshold.fixed_value"]   = str(thresh_cfg.get("fixed_value", 0.5))

    calib_cfg = cfg.get("calibration", {})
    params["calibration.enabled"]  = str(calib_cfg.get("enabled", False))
    params["calibration.method"]   = str(calib_cfg.get("method", "none"))
    params["calibration.fraction"] = str(calib_cfg.get("calib_fraction", 0.20))

    return params


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(config_path: Path) -> None:
    cfg = _load_config(config_path)

    data_cfg   = cfg["data"]
    split_cfg  = cfg["split"]
    feat_cfg   = cfg["features"]
    prep_cfg   = cfg["preprocessing"]
    model_cfg  = cfg["model"]
    thresh_cfg = cfg["threshold"]
    calib_cfg  = cfg.get("calibration", {"enabled": False})

    raw_path  = data_cfg["path"]
    data_path = Path(raw_path) if Path(raw_path).is_absolute() else _PROJECT_ROOT / raw_path

    model_dir   = _PROJECT_ROOT / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    output_name  = model_cfg.get("output_name", "fraud_model")
    model_path   = model_dir / f"{output_name}.pkl"
    prep_path    = model_dir / f"{output_name}_prep.pkl"
    calib_path   = model_dir / f"{output_name}_calibrated.pkl"
    meta_path    = model_dir / "model_meta.json"   # fixed name — scoring service reads this

    # --- MLflow setup (start run early so params/metrics land in one run) ---
    mlflow_cfg    = cfg.get("mlflow", {})
    _tracking_uri = mlflow_cfg.get("tracking_uri", "sqlite:///mlflow.db")
    # Resolve bare relative paths (e.g. "mlruns") to absolute, but leave
    # scheme-prefixed URIs (http://, sqlite:///, postgresql://, etc.) untouched.
    if "://" not in _tracking_uri:
        _tracking_uri = str(_PROJECT_ROOT / _tracking_uri)
    mlflow.set_tracking_uri(_tracking_uri)
    mlflow.set_experiment(mlflow_cfg.get("experiment_name", "fraud-detection"))
    _run_name    = mlflow_cfg.get("run_name") or f"{model_cfg.get('type', 'model')}_{output_name}"
    _mlflow_tags = {str(k): str(v) for k, v in (mlflow_cfg.get("tags") or {}).items()}
    _mlflow_run  = mlflow.start_run(run_name=_run_name, tags=_mlflow_tags)
    print(f"MLflow run started  → {_mlflow_run.info.run_id}")

    label_col    = data_cfg.get("label_col", "is_fraud")
    ts_col       = data_cfg.get("timestamp_col", "event_timestamp")
    fillna_val   = data_cfg.get("fillna_value", 0)
    numeric_cols = list(feat_cfg.get("numeric", []))
    cat_cols     = list(feat_cfg.get("categorical", []))
    all_features = numeric_cols + cat_cols

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    print(f"Loading training data from {data_path} ...")
    df = pd.read_parquet(data_path)

    missing = [c for c in all_features if c not in df.columns]
    if missing:
        print(f"  WARNING: {len(missing)} feature(s) not in dataset — filling with {fillna_val}:")
        for m in missing:
            print(f"    - {m}")
            df[m] = fillna_val
    df[all_features] = df[all_features].fillna(fillna_val)

    # ------------------------------------------------------------------
    # Temporal or random split
    # ------------------------------------------------------------------
    split_method = split_cfg.get("method", "temporal")
    if split_method == "temporal":
        train_df, val_df, split_cutoff = _temporal_split(df, ts_col, split_cfg)
    else:
        train_df, val_df, split_cutoff = _random_split(df, label_col, split_cfg)

    # ------------------------------------------------------------------
    # Calibration set — carved from training BEFORE base model training
    # ------------------------------------------------------------------
    calib_enabled = calib_cfg.get("enabled", False)
    calib_method  = calib_cfg.get("method", "none")
    if calib_method == "none":
        calib_enabled = False

    if calib_enabled:
        calib_fraction = float(calib_cfg.get("calib_fraction", 0.20))
        calib_seed     = int(calib_cfg.get("calib_seed", 42))
        base_train_df, calib_df = _carve_calibration_set(
            train_df, label_col, calib_fraction, calib_seed, split_method
        )
    else:
        base_train_df = train_df
        calib_df      = None

    X_train = base_train_df[all_features]
    y_train = base_train_df[label_col].astype(int)
    X_val   = val_df[all_features]
    y_val   = val_df[label_col].astype(int)

    fraud_tr  = int(y_train.sum())
    legit_tr  = len(y_train) - fraud_tr
    fraud_val = int(y_val.sum())
    legit_val = len(y_val) - fraud_val
    pos_weight = legit_tr / max(fraud_tr, 1)

    split_label = "OOT" if split_method == "temporal" else "Val"
    print(f"Base train balance  : fraud={fraud_tr:,}  legit={legit_tr:,}  pos_weight={pos_weight:.1f}")
    print(f"{split_label:5} balance      : fraud={fraud_val:,}  legit={legit_val:,}")

    # --- Log all experiment params now that we have pos_weight ---
    mlflow.log_params(_collect_mlflow_params(cfg, model_cfg["type"], numeric_cols, cat_cols, pos_weight))
    mlflow.log_params({
        "data.n_train":          str(len(X_train)),
        "data.n_val":            str(len(X_val)),
        "data.n_fraud_train":    str(fraud_tr),
        "data.n_fraud_val":      str(fraud_val),
        "data.fraud_rate_train": str(round(fraud_tr / max(len(y_train), 1), 4)),
        "data.fraud_rate_val":   str(round(fraud_val / max(len(y_val), 1), 4)),
    })

    # ------------------------------------------------------------------
    # Preprocessing — fit ONLY on base training data
    # ------------------------------------------------------------------
    preprocessor = _build_preprocessor(numeric_cols, cat_cols, prep_cfg)
    if preprocessor is not None:
        num_t = prep_cfg.get("numeric_transformer", "passthrough")
        cat_t = prep_cfg.get("categorical_transformer", "passthrough")
        print(f"Fitting preprocessor : numeric={num_t}  categorical={cat_t}")
        X_train_t = preprocessor.fit_transform(X_train)
        X_val_t   = preprocessor.transform(X_val)
        X_calib_t = preprocessor.transform(calib_df[all_features]) if calib_df is not None else None
        try:
            feature_names_out = list(preprocessor.get_feature_names_out())
        except Exception:
            feature_names_out = [f"f{i}" for i in range(X_train_t.shape[1])]
    else:
        X_train_t = X_train.to_numpy()
        X_val_t   = X_val.to_numpy()
        X_calib_t = calib_df[all_features].to_numpy() if calib_df is not None else None
        feature_names_out = all_features

    # ------------------------------------------------------------------
    # Train model
    # ------------------------------------------------------------------
    model, es_rounds = _build_model(model_cfg, pos_weight)
    model_type = model_cfg["type"]
    print(f"Training {model_type} model ...")

    if model_type == "xgboost":
        model.fit(
            X_train_t, y_train,
            eval_set=[(X_val_t, y_val)],
            verbose=50,
        )
    elif model_type == "lightgbm":
        from lightgbm import early_stopping as _lgbm_es, log_evaluation as _lgbm_log
        model.fit(
            X_train_t, y_train,
            eval_set=[(X_val_t, y_val)],
            callbacks=[_lgbm_es(es_rounds, verbose=True), _lgbm_log(50)],
        )
    else:   # random_forest — no early stopping
        model.fit(X_train_t, y_train)

    # ------------------------------------------------------------------
    # Calibration — fit on held-out calib set (cv='prefit')
    # ------------------------------------------------------------------
    calibrated_model = None
    if calib_enabled and X_calib_t is not None:
        y_calib = calib_df[label_col].astype(int).to_numpy()
        print(f"\nFitting calibrator ({calib_method}) ...")
        calibrated_model = _fit_calibrator(model, X_calib_t, y_calib, calib_method)

    # Scoring model: prefer calibrated if available
    scoring_model = calibrated_model if calibrated_model is not None else model

    # ------------------------------------------------------------------
    # Evaluate on OOT / val
    # ------------------------------------------------------------------
    raw_proba = model.predict_proba(X_val_t)[:, 1]
    val_proba = scoring_model.predict_proba(X_val_t)[:, 1]
    roc_auc   = roc_auc_score(y_val, val_proba)
    pr_auc    = average_precision_score(y_val, val_proba)

    if calibrated_model is not None:
        raw_roc = roc_auc_score(y_val, raw_proba)
        raw_pr  = average_precision_score(y_val, raw_proba)
        print(f"\n{split_label} metrics (raw)        → ROC-AUC={raw_roc:.4f}  PR-AUC={raw_pr:.4f}")
        print(f"{split_label} metrics (calibrated) → ROC-AUC={roc_auc:.4f}  PR-AUC={pr_auc:.4f}")
    else:
        print(f"\n{split_label} metrics → ROC-AUC={roc_auc:.4f}  PR-AUC={pr_auc:.4f}")

    threshold = _choose_threshold(y_val.to_numpy(), val_proba, thresh_cfg)
    t_strategy = thresh_cfg.get("strategy", "recall_target")
    t_param    = thresh_cfg.get("target_recall") if t_strategy == "recall_target" else thresh_cfg.get("fixed_value")
    print(f"Threshold ({t_strategy}={t_param}) : {threshold:.4f}")

    # --- Log training-time metrics ---
    _train_metrics: dict[str, float] = {
        "train.val_roc_auc": roc_auc,
        "train.val_pr_auc":  pr_auc,
        "train.threshold":   threshold,
    }
    if calibrated_model is not None:
        _train_metrics["train.val_roc_auc_raw"] = raw_roc
        _train_metrics["train.val_pr_auc_raw"]  = raw_pr
    mlflow.log_metrics(_train_metrics)

    # ------------------------------------------------------------------
    # Save artifacts
    # ------------------------------------------------------------------
    print(f"\nSaving base model   → {model_path}")
    joblib.dump(model, model_path)

    joblib.dump(preprocessor, prep_path)   # None when passthrough
    print(f"Saving preprocessor → {prep_path}")

    if calibrated_model is not None:
        joblib.dump(calibrated_model, calib_path)
        print(f"Saving calibrated   → {calib_path}")
    else:
        # Remove stale calibrated artifact to avoid confusion
        if calib_path.exists():
            calib_path.unlink()
            print(f"Removed stale calibrated artifact: {calib_path}")

    # --- Log model artifact + optionally register in MLflow Model Registry ---
    # mlflow.sklearn.log_model logs a proper MLmodel directory (required for
    # register_model in MLflow 3.x) and works with local/SQLite backends.
    # Raw pkl files are also logged separately under artifacts/ so the existing
    # scoring service can load them by path without depending on MLflow at runtime.
    _registry_name = (
        mlflow_cfg.get("model_registry_name", output_name)
        if mlflow_cfg.get("register_model", True) else None
    )
    print(f"Logging model to MLflow{' (registry: ' + _registry_name + ')' if _registry_name else ''} ...")
    mlflow.sklearn.log_model(
        sk_model=scoring_model,
        name=output_name,
        registered_model_name=_registry_name,
    )
    # Supplementary raw pkl artifacts for the scoring service
    mlflow.log_artifact(str(model_path), artifact_path="artifacts")
    if prep_path.exists():
        mlflow.log_artifact(str(prep_path), artifact_path="artifacts")
    if calib_path.exists():
        mlflow.log_artifact(str(calib_path), artifact_path="artifacts")
    # Log training config file so the exact setup is reproducible from the UI
    mlflow.log_artifact(str(config_path), artifact_path="config")
    # Log feature importances as a JSON artifact for easy comparison across runs
    if hasattr(model, "feature_importances_"):
        _fi_pairs = sorted(
            zip(feature_names_out[:len(model.feature_importances_)], model.feature_importances_.tolist()),
            key=lambda x: x[1], reverse=True,
        )
        mlflow.log_dict(dict(_fi_pairs), "feature_importances.json")

    n_estimators_used = None
    if model_type == "xgboost":
        n_estimators_used = getattr(model, "best_iteration", None) or model_cfg.get("xgboost", {}).get("n_estimators")
    elif model_type == "lightgbm":
        n_estimators_used = getattr(model, "best_iteration_", None) or model_cfg.get("lightgbm", {}).get("n_estimators")

    meta = {
        "model_name":  output_name,
        "model_type":  model_type,
        # --- split info (used by evaluate_model.py to reproduce exact split) ---
        "split_method":           split_method,
        "split_cutoff":           split_cutoff,
        "split_random_test_size": split_cfg.get("random_test_size", 0.20) if split_method == "random" else None,
        "split_random_seed":      split_cfg.get("random_seed", 42)        if split_method == "random" else None,
        # --- data info ---
        "data_path":      str(raw_path),
        "label_col":      label_col,
        "timestamp_col":  ts_col,
        # --- feature info ---
        "feature_cols":      all_features,
        "feature_names_out": feature_names_out,
        "n_features":        len(all_features),
        # --- preprocessing ---
        "numeric_transformer":     prep_cfg.get("numeric_transformer", "passthrough"),
        "categorical_transformer": prep_cfg.get("categorical_transformer", "passthrough"),
        # --- calibration ---
        "calibration_enabled": calib_enabled,
        "calibration_method":  calib_method if calib_enabled else "none",
        "calib_fraction":      float(calib_cfg.get("calib_fraction", 0.20)) if calib_enabled else None,
        # --- metrics and threshold (on calibrated proba if calibration enabled) ---
        "threshold":         threshold,
        "val_roc_auc":       round(roc_auc, 4),
        "val_pr_auc":        round(pr_auc, 4),
        "n_estimators_used": n_estimators_used,
        # MLflow back-reference so evaluate_model.py can resume this run
        "mlflow_run_id":      _mlflow_run.info.run_id,
        "mlflow_tracking_uri": _tracking_uri,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    # Also log model_meta.json as a config artifact for full reproducibility
    mlflow.log_artifact(str(meta_path), artifact_path="config")
    print(f"Saving metadata     → {meta_path}")

    mlflow.end_run()
    print(f"MLflow run complete → {_mlflow_run.info.run_id}")
    print("\nTraining complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train fraud detection model")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"Path to training config YAML (default: {DEFAULT_CONFIG})",
    )
    args = parser.parse_args()
    main(args.config)
