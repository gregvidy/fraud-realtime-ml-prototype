# Training Pipeline — Fraud Real-Time ML Prototype

## Overview

The training pipeline is **config-driven** and **MLflow-integrated**. A single YAML config file controls the model type, hyperparameters, preprocessing, calibration strategy, and evaluation criteria. All experiments are tracked in MLflow with full reproducibility.

---

## Training Pipeline Flow

```mermaid
flowchart TD
    subgraph DATA_PREP["1. Data Preparation"]
        PG["PostgreSQL<br/>(Raw Tables)"]
        EXPORT["export_pg_to_duckdb.py<br/>Raw → DuckDB"]
        DUCKDB["DuckDB<br/>(Analytical Store)"]
        DBT["dbt run<br/>(6 staging → 7 intermediate<br/>→ 4 feature models)"]
        TRAIN_DS["fct_training_dataset<br/>(41 features + is_fraud label)"]
        BUILD["build_training_dataset.py<br/>(Optional: --sample-frac 0.3)"]
        PARQUET["training_dataset.parquet"]
    end

    subgraph TRAINING["2. Model Training"]
        CONFIG["Experiment Config<br/>(YAML)"]
        SPLIT["Temporal / Random Split<br/>(OOT validation)"]
        CALIB_SPLIT["Calibration Holdout<br/>(20% of train)"]
        BASE_TRAIN["Base Model Training<br/>(LightGBM / XGBoost / RF)"]
        CALIBRATE["Probability Calibration<br/>(Isotonic / Sigmoid / Beta)"]
        THRESHOLD["Threshold Selection<br/>(Recall target or fixed)"]
    end

    subgraph EVAL["3. Evaluation"]
        METRICS["Metrics Computation<br/>ROC-AUC, PR-AUC, Brier,<br/>ECE, Recall@Precision"]
        CALIB_DIAG["Calibration Diagnostics<br/>(Reliability Diagram)"]
        FEAT_IMP["Feature Importance<br/>(Top 20)"]
    end

    subgraph REGISTRY["4. Registration & Promotion"]
        MLFLOW["MLflow<br/>(Log Params + Metrics + Artifacts)"]
        PROMOTE["promote_model.py<br/>(Champion Selection)"]
        MODEL_DIR["models/<br/>*.pkl + model_meta.json"]
        API["Scoring API<br/>(Hot Reload)"]
    end

    PG --> EXPORT --> DUCKDB --> DBT --> TRAIN_DS --> BUILD --> PARQUET
    CONFIG --> SPLIT
    PARQUET --> SPLIT
    SPLIT --> CALIB_SPLIT --> BASE_TRAIN --> CALIBRATE --> THRESHOLD
    THRESHOLD --> METRICS --> MLFLOW
    THRESHOLD --> CALIB_DIAG --> MLFLOW
    THRESHOLD --> FEAT_IMP --> MLFLOW
    MLFLOW --> PROMOTE --> MODEL_DIR --> API

    style DATA_PREP fill:#1a3d5c,stroke:#2980b9,color:#fff
    style TRAINING fill:#4a3560,stroke:#7d5da0,color:#fff
    style EVAL fill:#5c3d1a,stroke:#b97029,color:#fff
    style REGISTRY fill:#2d5016,stroke:#4a8c2a,color:#fff
```

---

## Experiment Configuration (YAML-Driven)

All training parameters are defined in a single YAML file under `training/experiments/`:

```yaml
# training/experiments/lgbm_optimized_hyperparams.yaml
model:
  type: lightgbm                    # lightgbm | xgboost | random_forest
  params:
    n_estimators: 3000
    num_leaves: 127
    learning_rate: 0.01
    min_child_samples: 10
    reg_alpha: 0.1
    reg_lambda: 5.0
    metric: average_precision
    early_stopping_rounds: 100

preprocessing:
  numeric:
    strategy: passthrough           # passthrough | standard_scaler | minmax | robust
  categorical:
    strategy: passthrough           # passthrough | ordinal | one_hot

calibration:
  method: isotonic                  # isotonic | sigmoid | beta
  fraction: 0.20                    # fraction of train set held for calibration

split:
  method: temporal                  # temporal | random
  test_size: 0.20
  temporal_quantile: 0.80           # use top 20% as OOT validation

threshold:
  method: recall_target             # recall_target | fixed
  recall_target: 0.80              # find threshold achieving 80% recall

output_name: lgbm_optimized_model
```

---

## Training Data Split Strategy

```mermaid
flowchart LR
    subgraph TEMPORAL["Temporal (Out-of-Time) Split"]
        direction LR
        HIST["Historical Data<br/>← 80% by time →"]
        OOT["OOT Validation<br/>← 20% by time →"]
    end

    subgraph WITHIN_TRAIN["Within Training Set"]
        direction LR
        BASE["Base Model Training<br/>(80% of train)"]
        CAL["Calibration Holdout<br/>(20% of train)"]
    end

    HIST --> BASE
    HIST --> CAL
    OOT --> |"evaluate_model.py"| EVAL_OOT["OOT Evaluation<br/>(Unseen future data)"]

    style HIST fill:#2980b9,color:#fff
    style OOT fill:#e74c3c,color:#fff
    style BASE fill:#27ae60,color:#fff
    style CAL fill:#f39c12,color:#fff
```

**Why Temporal Split?** Fraud patterns evolve over time. A temporal (OOT) split simulates real-world deployment where the model always scores **future** transactions it hasn't seen during training. This gives a more honest estimate of production performance than random splits.

---

## Model Artifacts

Each training run produces 4 files:

| File | Contents |
|------|----------|
| `{name}.pkl` | Base LightGBM model (uncalibrated) |
| `{name}_calibrated.pkl` | `CalibratedClassifierCV` wrapper |
| `{name}_prep.pkl` | `ColumnTransformer` preprocessor |
| `model_meta.json` | Feature list, threshold, metrics, split config, MLflow run ID |

### model_meta.json Example
```json
{
  "model_name": "lgbm_optimized_model",
  "model_type": "lightgbm",
  "feature_cols": ["txn_amount", "is_international", "local_hour", "user_account_age_days", ...],
  "threshold": 0.006463,
  "calibration": {"method": "isotonic", "fraction": 0.20},
  "val_metrics": {"roc_auc": 0.7288, "pr_auc": 0.4683, "brier_score": 0.0089},
  "mlflow_run_id": "abc123..."
}
```

---

## MLflow Integration

```mermaid
flowchart LR
    TRAIN["train_model.py"] -->|Log| MLFLOW["MLflow Server<br/>(sqlite:///mlflow.db)"]
    MLFLOW -->|Store| ARTIFACTS["mlruns/<br/>Model Artifacts"]
    MLFLOW -->|Register| REGISTRY["Model Registry<br/>(fraud-model)"]

    EVAL["evaluate_model.py"] -->|"Log OOT Metrics"| MLFLOW

    CLI["promote_model.py<br/>--run-id abc123"] -->|Download Artifacts| MODELS["models/<br/>(Active Model)"]
    CLI -->|Tag: champion| REGISTRY

    subgraph TRACKED["What Gets Tracked"]
        PARAMS["Parameters<br/>n_estimators, lr,<br/>num_leaves, ..."]
        METRICS_T["Train Metrics<br/>ROC-AUC, PR-AUC,<br/>Brier, ECE"]
        METRICS_V["Val Metrics<br/>ROC-AUC, PR-AUC,<br/>Recall@Precision"]
        ARTS["Artifacts<br/>model.pkl, prep.pkl,<br/>calibrated.pkl,<br/>meta.json, config.yaml"]
    end

    MLFLOW --- TRACKED
```

### Key MLflow Commands

```bash
# View experiment history
make mlflow-ui              # Opens http://localhost:5000

# List recent runs with metrics
make list-models

# Promote a run to active model
make promote-model RUN_ID=<run_id>

# Set model aliases (champion/challenger/archived)
make alias-model MODEL=fraud-model VERSION=3 ALIAS=champion
```

---

## Supported Model Types

| Model | Config Key | Inference Time | Training Time (50K rows) |
|-------|-----------|---------------|--------------------------|
| **LightGBM** | `lightgbm` | ~1ms | ~30s |
| **XGBoost** | `xgboost` | ~2ms | ~45s |
| **Random Forest** | `random_forest` | ~5ms | ~60s |

---

## Calibration Pipeline

```mermaid
flowchart LR
    RAW["Raw predict_proba<br/>(may be poorly calibrated)"]
    ISO["Isotonic Regression<br/>(non-parametric)"]
    EXTRACT["Extract Calibration<br/>as 1000-point grid<br/>(np arrays)"]
    FAST["Fast np.interp<br/>(~µs at inference)"]

    RAW --> ISO --> EXTRACT --> FAST

    subgraph ALTERNATIVES["Alternative Methods"]
        SIG["Sigmoid (Platt Scaling)"]
        BETA["Beta Calibration"]
    end

    RAW --> SIG
    RAW --> BETA
```

**Key optimization**: At model load time, the isotonic calibration mapping is extracted from sklearn's `CalibratedClassifierCV` into plain numpy arrays. At inference, `np.interp()` replaces the full sklearn predict call — reducing calibration from **15-40ms to < 0.01ms**.

---

## Quick Reference

```bash
# Full pipeline: data → features → train → evaluate
make train CONFIG=training/experiments/lgbm_optimized_hyperparams.yaml

# Train only (reuse existing dataset)
make train-only CONFIG=training/experiments/lgbm_v1.yaml

# Train with subsampled data (faster iteration)
make train SAMPLE=0.3

# Train with CPU isolation (won't affect serving)
make train-isolated CONFIG=training/experiments/lgbm_v1.yaml

# Evaluate an existing model on OOT data
python training/evaluate_model.py
```
