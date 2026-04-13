"""
evaluate_model.py
-----------------
Evaluates the trained model on the held-out OOT / validation split.

Reads split configuration from model_meta.json so that evaluation always
reproduces the exact same split used during training — no config file needed.

When a calibrated artifact ({model_name}_calibrated.pkl) exists, metrics and
the reliability diagram are shown for both raw and calibrated probabilities.

Usage:
    python training/evaluate_model.py
    python training/evaluate_model.py --meta path/to/model_meta.json
"""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)

_PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_META  = _PROJECT_ROOT / "models" / "model_meta.json"


# ---------------------------------------------------------------------------
# Calibration diagnostics helpers
# ---------------------------------------------------------------------------

def _ece(y_true: np.ndarray, proba: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error (equal-width bins)."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece  = 0.0
    n    = len(y_true)
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (proba >= lo) & (proba < hi)
        if mask.sum() == 0:
            continue
        acc  = y_true[mask].mean()
        conf = proba[mask].mean()
        ece += (mask.sum() / n) * abs(acc - conf)
    return float(ece)


def _reliability_diagram(
    y_true: np.ndarray,
    proba: np.ndarray,
    label: str = "",
    n_bins: int = 10,
    width: int = 50,
) -> None:
    """Print an ASCII reliability diagram to stdout."""
    frac_pos, mean_pred = calibration_curve(y_true, proba, n_bins=n_bins, strategy="uniform")
    header = f"  Reliability Diagram{' (' + label + ')' if label else ''}"
    print(header)
    print(f"  {'Pred prob':<12} {'Actual freq':>11}  Bar")
    print("  " + "-" * (width + 26))
    for pred, actual in zip(mean_pred, frac_pos):
        bar_len = int(actual * width)
        ref_len = int(pred * width)
        bar = "█" * bar_len
        marker = "│"         # perfect-calibration reference marker
        # Overlay the perfect-calibration line
        col_ref = min(ref_len, width - 1)
        if col_ref >= bar_len:
            bar = bar.ljust(col_ref) + marker
        print(f"  {pred:>6.2f}–{pred:>5.2f}   {actual:>8.4f}   {bar}")
    print()


def _calibration_summary(
    y_val: np.ndarray,
    proba: np.ndarray,
    label: str,
    n_bins: int = 10,
) -> None:
    brier = brier_score_loss(y_val, proba)
    ece   = _ece(y_val, proba, n_bins=n_bins)
    print(f"  Brier score  ({label}): {brier:.6f}  (lower is better)")
    print(f"  ECE          ({label}): {ece:.6f}  (lower is better)")


# ---------------------------------------------------------------------------
# Split reproduction
# ---------------------------------------------------------------------------

def _reproduce_split(
    df: pd.DataFrame, meta: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reproduce train/OOT split from saved metadata."""
    method    = meta.get("split_method", "temporal")
    label_col = meta.get("label_col", "is_fraud")
    ts_col    = meta.get("timestamp_col", "event_timestamp")

    if method == "temporal":
        cutoff = pd.Timestamp(meta["split_cutoff"])
        df = df.sort_values(ts_col).reset_index(drop=True)
        return df[df[ts_col] < cutoff], df[df[ts_col] >= cutoff]

    # random — reproduce with same seed and size
    from sklearn.model_selection import train_test_split
    test_size = float(meta.get("split_random_test_size", 0.20))
    seed      = int(meta.get("split_random_seed", 42))
    return train_test_split(
        df, test_size=test_size, stratify=df[label_col], random_state=seed
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(meta_path: Path) -> None:
    meta = json.loads(meta_path.read_text())

    output_name       = meta.get("model_name", "fraud_model")
    model_dir         = meta_path.parent
    model_path        = model_dir / f"{output_name}.pkl"
    prep_path         = model_dir / f"{output_name}_prep.pkl"
    calib_path        = model_dir / f"{output_name}_calibrated.pkl"

    feature_cols      = meta["feature_cols"]
    feature_names_out = meta.get("feature_names_out", feature_cols)
    threshold         = meta["threshold"]
    label_col         = meta.get("label_col", "is_fraud")
    fillna_val        = 0

    # ------------------------------------------------------------------
    # Load data and reproduce exact split from training
    # ------------------------------------------------------------------
    raw_path  = meta.get("data_path", "training/datasets/training_dataset.parquet")
    data_path = Path(raw_path) if Path(raw_path).is_absolute() else _PROJECT_ROOT / raw_path
    print(f"Loading data from {data_path} ...")
    df = pd.read_parquet(data_path)

    missing = [c for c in feature_cols if c not in df.columns]
    for m in missing:
        df[m] = fillna_val
    df[feature_cols] = df[feature_cols].fillna(fillna_val)

    _, val_df = _reproduce_split(df, meta)

    split_method = meta.get("split_method", "temporal")
    split_label  = "OOT" if split_method == "temporal" else "Val"
    y_val = val_df[label_col].astype(int).to_numpy()
    print(f"{split_label} set: {len(val_df):,} rows  fraud={y_val.sum():,}  ({y_val.mean()*100:.2f}%)")

    # ------------------------------------------------------------------
    # Load model + preprocessor + calibrated model
    # ------------------------------------------------------------------
    print(f"Loading base model from {model_path} ...")
    base_model = joblib.load(model_path)

    preprocessor = None
    if prep_path.exists():
        preprocessor = joblib.load(prep_path)

    calibrated_model = None
    if calib_path.exists():
        calibrated_model = joblib.load(calib_path)
        calib_label = meta.get("calibration_method", "calibrated")
        print(f"Calibrated model loaded ({calib_label}) from {calib_path}")

    X_val = val_df[feature_cols]
    if preprocessor is not None:
        X_val_t = preprocessor.transform(X_val)
    else:
        X_val_t = X_val.to_numpy()

    # ------------------------------------------------------------------
    # Compute raw and calibrated probabilities
    # ------------------------------------------------------------------
    raw_proba  = base_model.predict_proba(X_val_t)[:, 1]
    if calibrated_model is not None:
        cal_proba = calibrated_model.predict_proba(X_val_t)[:, 1]
        scoring_proba = cal_proba
    else:
        cal_proba     = None
        scoring_proba = raw_proba

    preds   = (scoring_proba >= threshold).astype(int)
    roc_auc = roc_auc_score(y_val, scoring_proba)
    pr_auc  = average_precision_score(y_val, scoring_proba)

    precision, recall, _ = precision_recall_curve(y_val, scoring_proba)
    valid = precision >= 0.5
    recall_at_p50 = recall[valid].max() if valid.any() else 0.0

    model_type = meta.get("model_type", "unknown")

    # ------------------------------------------------------------------
    # Print performance report
    # ------------------------------------------------------------------
    print("\n" + "=" * 66)
    print(f"Model  : {output_name}  ({model_type})")
    print(f"Split  : {split_method}  ({split_label} = {len(val_df):,} rows)")
    calibration_info = meta.get("calibration_method", "none")
    print(f"Calib  : {calibration_info}")
    print(f"{'ROC-AUC':<40}: {roc_auc:.4f}")
    print(f"{'PR-AUC':<40}: {pr_auc:.4f}")
    print(f"{'Recall @ Precision\u22650.5':<40}: {recall_at_p50:.4f}")
    print(f"{'Threshold':<40}: {threshold:.4f}")
    print()
    print("Classification Report:")
    print(classification_report(y_val, preds, target_names=["legit", "fraud"]))
    print("Confusion Matrix:")
    cm = confusion_matrix(y_val, preds)
    print(f"  TN={cm[0, 0]:>6,}  FP={cm[0, 1]:>6,}")
    print(f"  FN={cm[1, 0]:>6,}  TP={cm[1, 1]:>6,}")

    # ------------------------------------------------------------------
    # Calibration diagnostics
    # ------------------------------------------------------------------
    print("\n" + "-" * 66)
    print("Calibration Diagnostics")
    print()
    _calibration_summary(y_val, raw_proba, label="raw")
    if cal_proba is not None:
        _calibration_summary(y_val, cal_proba, label=f"calibrated/{calib_label}")
    print()
    _reliability_diagram(y_val, raw_proba, label="raw")
    if cal_proba is not None:
        _reliability_diagram(y_val, cal_proba, label=f"calibrated/{calib_label}")

    # ------------------------------------------------------------------
    # Feature importance (top 20)
    # ------------------------------------------------------------------
    src_model = base_model   # importances always from the unwrapped base model
    if hasattr(src_model, "feature_importances_"):
        importances = src_model.feature_importances_
        names = list(feature_names_out)[:len(importances)]
        top_pairs = sorted(zip(names, importances), key=lambda x: x[1], reverse=True)[:20]
        print("-" * 66)
        print("Top 20 Feature Importances (base model):")
        for name, imp in top_pairs:
            bar = "█" * int(imp * 300)
            print(f"  {name:<45} {imp:.5f} {bar}")

    print("=" * 66)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate trained fraud model")
    parser.add_argument(
        "--meta", type=Path, default=DEFAULT_META,
        help=f"Path to model_meta.json (default: {DEFAULT_META})",
    )
    args = parser.parse_args()
    main(args.meta)
