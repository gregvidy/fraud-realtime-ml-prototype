"""
evaluate_model.py
-----------------
Evaluates the trained model against the held-out validation split
and prints a full evaluation summary.

Metrics: ROC-AUC, PR-AUC, Recall@Precision=0.5, confusion matrix.
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
    precision_recall_curve,
)
from sklearn.model_selection import train_test_split

DATA_PATH  = Path(__file__).parent / "datasets" / "training_dataset.parquet"
MODEL_PATH = Path(__file__).parent.parent / "models" / "fraud_model.pkl"
META_PATH  = Path(__file__).parent.parent / "models" / "model_meta.json"

LABEL_COL = "is_fraud"


def main() -> None:
    meta = json.loads(META_PATH.read_text())
    feature_cols = meta["feature_cols"]
    threshold    = meta["threshold"]

    print(f"Loading data from {DATA_PATH}...")
    df = pd.read_parquet(DATA_PATH)
    df[feature_cols] = df[feature_cols].fillna(0)

    X = df[feature_cols].astype(float)
    y = df[LABEL_COL].astype(int)

    _, X_val, _, y_val = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    print(f"Loading model from {MODEL_PATH}...")
    model = joblib.load(MODEL_PATH)

    proba = model.predict_proba(X_val)[:, 1]
    preds = (proba >= threshold).astype(int)

    roc_auc = roc_auc_score(y_val, proba)
    pr_auc  = average_precision_score(y_val, proba)

    precision, recall, thresholds = precision_recall_curve(y_val, proba)

    # Recall at precision >= 0.5
    valid = precision >= 0.5
    recall_at_p50 = recall[valid].max() if valid.any() else 0.0

    print("\n" + "=" * 50)
    print(f"Model: {meta['model_name']}")
    print(f"{'ROC-AUC':<30}: {roc_auc:.4f}")
    print(f"{'PR-AUC':<30}: {pr_auc:.4f}")
    print(f"{'Recall @ Precision≥0.5':<30}: {recall_at_p50:.4f}")
    print(f"{'Threshold used':<30}: {threshold:.4f}")
    print()
    print("Classification Report (val set):")
    print(classification_report(y_val, preds, target_names=["legit", "fraud"]))
    print("Confusion Matrix:")
    cm = confusion_matrix(y_val, preds)
    print(f"  TN={cm[0,0]:>6}  FP={cm[0,1]:>6}")
    print(f"  FN={cm[1,0]:>6}  TP={cm[1,1]:>6}")
    print()

    # Feature importance top-15
    importance = dict(zip(feature_cols, model.feature_importances_))
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:15]
    print("Top 15 features by importance:")
    for feat, imp in top_features:
        bar = "█" * int(imp * 200)
        print(f"  {feat:<40} {imp:.4f} {bar}")
    print("=" * 50)


if __name__ == "__main__":
    main()
