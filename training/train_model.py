"""
train_model.py
--------------
Trains a baseline XGBoost fraud detection model from the training Parquet.

Key decisions:
  - Stratified train/val split to preserve class imbalance ratio
  - scale_pos_weight to handle class imbalance (no resampling)
  - Early stopping on validation PR-AUC
  - Saves model artifact + feature column list

Output:
  models/fraud_model.pkl   — trained XGBoostClassifier
  models/model_meta.json   — metadata (version, feature list, threshold)
"""

import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split
from sklearn.metrics import average_precision_score, roc_auc_score
from xgboost import XGBClassifier

load_dotenv()

DATA_PATH  = Path(__file__).parent / "datasets" / "training_dataset.parquet"
MODEL_DIR  = Path(__file__).parent.parent / "models"
MODEL_PATH = MODEL_DIR / "fraud_model.pkl"
META_PATH  = MODEL_DIR / "model_meta.json"

# Feature columns — must match feature_contract.yaml and scoring.py
FEATURE_COLS = [
    # request-time
    "txn_amount", "is_international", "local_hour",
    # user offline
    "user_account_age_days", "user_is_verified", "user_is_standard_account",
    "user_txn_count_1d", "user_txn_count_7d", "user_txn_count_30d",
    "user_txn_amount_sum_1d", "user_txn_amount_sum_7d", "user_txn_amount_sum_30d",
    "user_avg_ticket_30d", "user_distinct_merchants_30d", "user_distinct_devices_30d",
    "user_decline_count_7d", "user_failed_logins_7d", "user_failed_logins_1d",
    # device offline
    "device_distinct_users_30d", "device_txn_count_7d", "device_txn_count_1d",
    "device_is_shared_flag",
    # merchant offline
    "merchant_is_high_risk", "merchant_is_online",
    "merchant_txn_count_30d", "merchant_avg_ticket_30d", "merchant_fraud_rate_30d",
    # Note: Redis online features not available in offline training data.
    # They are set to 0 during training and populated at serving time.
    # This is acceptable for the MVP baseline.
]

LABEL_COL = "is_fraud"


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading training data from {DATA_PATH}...")
    df = pd.read_parquet(DATA_PATH)

    # Fill nulls with sensible defaults
    df[FEATURE_COLS] = df[FEATURE_COLS].fillna(0)

    X = df[FEATURE_COLS].astype(float)
    y = df[LABEL_COL].astype(int)

    fraud_count   = y.sum()
    legit_count   = len(y) - fraud_count
    pos_weight    = legit_count / max(fraud_count, 1)
    print(f"Class balance: fraud={fraud_count:,}  legit={legit_count:,}  pos_weight={pos_weight:.1f}")

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    model = XGBClassifier(
        n_estimators=500,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=pos_weight,
        eval_metric="aucpr",
        early_stopping_rounds=30,
        tree_method="hist",
        random_state=42,
        verbosity=0,
    )

    print("Training XGBoost model...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=50,
    )

    val_proba = model.predict_proba(X_val)[:, 1]
    roc_auc   = roc_auc_score(y_val, val_proba)
    pr_auc    = average_precision_score(y_val, val_proba)
    print(f"\nValidation metrics → ROC-AUC={roc_auc:.4f}  PR-AUC={pr_auc:.4f}")

    # Choose threshold at recall=0.80 on validation
    from sklearn.metrics import precision_recall_curve
    precision, recall, thresholds = precision_recall_curve(y_val, val_proba)
    # Find threshold where recall >= 0.80
    target_recall = 0.80
    valid_idx = np.where(recall[:-1] >= target_recall)[0]
    if len(valid_idx) > 0:
        best_idx  = valid_idx[np.argmax(precision[valid_idx])]
        threshold = float(thresholds[best_idx])
    else:
        threshold = 0.5
    print(f"Chosen threshold (recall≥{target_recall}): {threshold:.4f}")

    print(f"Saving model → {MODEL_PATH}")
    joblib.dump(model, MODEL_PATH)

    meta = {
        "model_name":   "fraud_xgb_v1",
        "feature_cols": FEATURE_COLS,
        "threshold":    threshold,
        "val_roc_auc":  round(roc_auc, 4),
        "val_pr_auc":   round(pr_auc,  4),
        "n_features":   len(FEATURE_COLS),
        "n_estimators_used": model.best_iteration or model.n_estimators,
    }
    META_PATH.write_text(json.dumps(meta, indent=2))
    print(f"Saved metadata → {META_PATH}")
    print("Training complete.")


if __name__ == "__main__":
    main()
