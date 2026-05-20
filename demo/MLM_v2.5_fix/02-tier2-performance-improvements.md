# Tier 2: Performance Improvements — Month 2-3 (July–August 2026)

> **High Impact, Medium Complexity — Achieve sub-10ms scoring**

## Team Assignment — Tier 2

### Month 2 (July) — Scoring Path

| Change | Owner | Support | Calendar | Notes |
|--------|-------|---------|----------|-------|
| #6 FastAPI (Path A) | — | — | — | **Skipped if ONNX chosen (recommended)** |
| #7 ONNX Runtime scoring | Lead (ONNX export) + Engineer A (C# OnnxScoringService) | Engineer B (integration tests) | Week 5-6 | Lead teaches ONNX concept in Week 5 Day 1 |
| #8 Model hot-reload | Engineer A | Lead (review) | Week 5-6 | C# FileSystemWatcher — straightforward |
| #9 DataHub pagination | Engineer B | Lead (review SQL pattern) | Week 7-8 | Familiar territory for full-stack eng |
| #10 Isotonic calibration | Engineer A | Lead (provides calibration math + test vectors) | Week 7-8 | ~20 lines C#, linear interpolation |

### Month 3 (August) — New Capabilities

| Change | Owner | Support | Calendar | Notes |
|--------|-------|---------|----------|-------|
| #11 Large dataset training | **Lead** | Engineer B (assists with DB pagination) | Week 9-10 | ML-specific: chunked Parquet, LightGBM binary |
| #12 XGBoost support | **Lead** | Engineer A (Angular dropdown + enum) | Week 9-10 | ML-specific: training + ONNX export |
| #13 GBM hyperparameters | Engineer A (Angular UI) + Engineer B (Python validation) | Lead (defines param ranges) | Week 9-10 | Low ML knowledge needed |
| #14 Backtesting | **Lead** (engine) + Engineer A (Angular UI) + Engineer B (API + DB) | — | Week 11-12 | Most complex item — all three contribute |

**Lead's role in Month 2**: Builds ONNX export pipeline (skl2onnx + onnxmltools), teaches engineers how ONNX works (1-hour session), provides test vectors for calibration, reviews all PRs.

**Lead's role in Month 3**: Owns all ML-heavy code (chunked training, XGBoost, backtesting scoring logic). Engineers handle UI/API/DB — they're now comfortable with the codebase.

---

## Change #6: Flask → FastAPI + Uvicorn

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Medium |
| **Effort** | 3-5 days |
| **Owner** | N/A — **Path A only (not recommended)** |
| **Calendar** | Skipped if ONNX path chosen |
| **Note** | Only implement if ONNX path is rejected after stakeholder discussion |

### Details

FastAPI + Uvicorn works on Windows:

```bash
uvicorn app:app --workers 4 --host 0.0.0.0 --port 5555
```

On Windows, Uvicorn uses `multiprocessing.spawn` (not `fork`).

All 12 Flask routes rewritten as FastAPI endpoints with Pydantic models. Async `await` for DB calls via `databases` or `encode/databases` library with aioodbc (SQL Server async).

> **Note**: This is **Path A** — see [04-option-comparison.md](04-option-comparison.md) for FastAPI vs ONNX decision.

---

## Change #7: ONNX Runtime for Scoring (Eliminate Python HTTP Hop)

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🔴 Critical |
| **Complexity** | Medium |
| **Effort** | 5-7 days |
| **Owner** | Lead (Python ONNX export + validation) + Engineer A (C# `OnnxScoringService.cs`) |
| **Calendar** | Month 2, Week 5-6 |
| **Prerequisite** | Lead prepares ONNX export script + sample .onnx file before Week 5 |
| **Knowledge transfer** | Lead runs 1-hour session: what ONNX is, how InferenceSession works, input/output tensor shapes |

### What It Does

Export trained LightGBM/RF/GBM models to ONNX. Load in C# via `Microsoft.ML.OnnxRuntime` NuGet. `ScoringService.cs` calls ONNX directly instead of `PythonService.ScoreAsync()`.

### What It Eliminates

- HTTP serialization overhead
- Python process dependency for scoring
- Pickle deserialization risk

### Performance

Scoring latency: ~500ms → **~5-10ms**. Python still needed for training only.

### Hybrid Routing (Recommended)

```csharp
// ScoringService.cs — hybrid routing
public async Task<JObject> ScoreAsync(ScorePayload payload)
{
    var model = _modelService.Get(payload.ModelId);

    if (model.Algorithm == ModelAlgorithm.Custom ||
        model.Algorithm == ModelAlgorithm.NeuralNetwork)
    {
        // Fallback: Python HTTP (same as v2.4)
        return await _pythonService.ScoreAsync(payload);
    }

    // Fast path: ONNX Runtime (v2.5)
    float[] features = _dataMappingService.GetFeatures(payload);
    float score = _onnxScoringService.Score(model.OnnxSessionId, features);
    float calibrated = _calibrationService.Calibrate(model.CalibrationProfile, score);

    return BuildScoreResponse(calibrated, model);
}
```

> **Note**: This is **Path B (Recommended)** — see [04-option-comparison.md](04-option-comparison.md) for full comparison.

---

## Change #8: Model Hot-Reload via File Watcher

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Low |
| **Effort** | 2 days |
| **Owner** | Engineer A |
| **Calendar** | Month 2, Week 5-6 |
| **Prerequisite** | None — `FileSystemWatcher` is built-in C#, Engineer A should be familiar |

### Details

Training writes `.onnx` (or `.pkl`) to a shared directory. Scoring service uses `FileSystemWatcher` (C# built-in) or `watchdog` (Python, already in codebase via `PluginsWatcher`). On file change → reload model into memory.

**Replaces the 10-day TTLCache** — model staleness drops from days to minutes.

---

## Change #9: DataHub Paginated Extraction

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Medium |
| **Effort** | 2-3 days |
| **Owner** | Engineer B |
| **Calendar** | Month 2, Week 7-8 |
| **Prerequisite** | Lead reviews current `CriteriaService` query pattern with Engineer B |

### Problem

Current approach pulls "5M rows in one shot" — causes significant DataHub I/O pressure.

### Fix

Modify `CriteriaService` to paginate training queries:

```sql
SELECT TOP 10000 ... WHERE id > @lastId ORDER BY id
```

Sleep 2s between batches. Reduces I/O pressure to "10K rows every 2s".

---

## Change #10: Isotonic Calibration in C# (ONNX Path)

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟡 Medium |
| **Complexity** | Low |
| **Effort** | 1 day |
| **Owner** | Engineer A |
| **Calendar** | Month 2, Week 7-8 |
| **Prerequisite** | Lead provides: (1) isotonic calibration explanation, (2) test vectors (input → expected output pairs) |

### Details

Extract isotonic calibration x/y arrays from sklearn at training time, store as JSON alongside ONNX model. C# scoring does linear interpolation (~20 lines). Enables calibrated probabilities without Python.

---

## Change #11: Large Dataset Training (>10M Rows)

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Medium |
| **Effort** | 3-4 days |
| **Owner** | **Lead** (core chunked pipeline) + Engineer B (DB pagination helper) |
| **Calendar** | Month 3, Week 9-10 |
| **Why Lead owns this** | Requires understanding of LightGBM Dataset internals, Parquet memory mapping, and training pipeline. Not something to delegate without ML background. |

### Problem

Current training loads the entire dataset from Read Replica into a pandas DataFrame in memory. At 10M rows × 50 features × 8 bytes = **~4 GB RAM** — exceeds available memory on most Windows Server deployments and causes OOM crashes or extreme swap thrashing.

### Fix: Chunked Ingestion → Parquet → LightGBM from File

No Python upgrade needed. No new infra. Works on Windows.

**Step 1: Stream from DB to local Parquet (constant ~500MB memory)**

```python
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Stream in 500K chunks — never holds full dataset in memory
chunks = pd.read_sql(
    "SELECT * FROM training_view WHERE id > @lastId ORDER BY id",
    conn,
    chunksize=500_000
)

writer = None
for chunk in chunks:
    table = pa.Table.from_pandas(chunk)
    if writer is None:
        writer = pq.ParquetWriter("train_data.parquet", table.schema)
    writer.write_table(table)
writer.close()
```

**Step 2: LightGBM reads from Parquet (memory-mapped, efficient)**

```python
import pyarrow.parquet as pq
import lightgbm as lgb

# PyArrow reads parquet with memory mapping — doesn't load all into RAM
table = pq.read_table("train_data.parquet")
df = table.to_pandas()  # Still needs RAM here — but can use column selection

# Alternative for truly massive datasets: LightGBM binary format
train_data = lgb.Dataset(
    df[feature_cols],
    label=df[label_col],
    free_raw_data=True  # Free the pandas DataFrame after building internal structure
)

model = lgb.train(params, train_data, valid_sets=[val_data])
```

**Step 3 (optional for >50M rows): Convert to LightGBM binary format**

```python
# Save as LightGBM binary — most memory-efficient format
train_data.save_binary("train_data.bin")

# On subsequent runs, load directly from binary (no pandas needed)
train_data = lgb.Dataset("train_data.bin")
```

### Memory Comparison

| Approach | Memory @ 10M rows × 50 features | Memory @ 50M rows |
|----------|----------------------------------|-------------------|
| Current (full pandas load) | ~4 GB | ~20 GB (OOM) |
| Chunked → Parquet → LightGBM | ~1-1.5 GB | ~3-5 GB |
| Chunked → LightGBM binary | ~500 MB | ~2 GB |

### Integration Points

- Modify `_train()` in `MachineLearning.py` to use chunked ingestion when row count exceeds threshold (e.g., >1M rows)
- Below threshold: keep existing pandas path (no regression for small datasets)
- Parquet temp file cleaned up after training completes
- Works with existing `CriteriaService` query — just wraps it in pagination

### Why Not Ray/Dask/Spark?

All require either Linux, cluster infrastructure, or significant dependency overhead — none are viable for a Windows-native v2.5. See [04-option-comparison.md](04-option-comparison.md) → Decision 6 for the full comparison.

---

## Change #12: XGBoost Algorithm Support

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Low-Medium |
| **Effort** | 2-3 days |
| **Owner** | **Lead** (Python training + ONNX export) + Engineer A (C# enum + Angular selector) |
| **Calendar** | Month 3, Week 9-10 |
| **Why Lead owns this** | XGBoost API, hyperparameter defaults, ONNX conversion via onnxmltools — all require ML knowledge. Engineer A only adds the UI dropdown + C# enum value. |

### What It Adds

XGBoost as a new algorithm option alongside existing LightGBM, Random Forest, and GBM. Clients can select XGBoost during model configuration.

### Implementation

**Python side** — add XGBoost to the algorithm registry in `MachineLearning.py`:

```python
import xgboost as xgb

def _train_xgboost(X_train, y_train, X_val, y_val, params):
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)

    default_params = {
        "objective": "binary:logistic",
        "eval_metric": "aucpr",
        "max_depth": 6,
        "learning_rate": 0.1,
        "n_estimators": 300,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5,
        "scale_pos_weight": ratio_negative / ratio_positive,
        "tree_method": "hist",  # Fast histogram-based (like LightGBM)
        "random_state": 42
    }
    # Override with user-specified params
    default_params.update(params)

    model = xgb.train(
        default_params,
        dtrain,
        num_boost_round=default_params.pop("n_estimators", 300),
        evals=[(dval, "validation")],
        early_stopping_rounds=50,
        verbose_eval=False
    )
    return model
```

**C# side** — add `XGBoost` to `ModelAlgorithm` enum and training configuration UI.

**ONNX export** — XGBoost has native ONNX support via `onnxmltools`:

```python
from onnxmltools import convert_xgboost
from onnxmltools.convert.common.data_types import FloatTensorType

initial_type = [("features", FloatTensorType([None, n_features]))]
onnx_model = convert_xgboost(model, initial_types=initial_type)
```

### Dependencies

| Package | Version | Note |
|---------|---------|------|
| `xgboost` | >=1.7 | pip install, Windows wheels available |
| `onnxmltools` | >=1.11 | For ONNX export (already needed for LightGBM ONNX path) |

---

## Change #13: Additional GBM Hyperparameters

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟡 Medium |
| **Complexity** | Low |
| **Effort** | 1-2 days |
| **Owner** | Engineer A (Angular form + collapsible section) + Engineer B (Python validation logic) |
| **Calendar** | Month 3, Week 9-10 |
| **Prerequisite** | Lead provides parameter range table and tooltip descriptions (already in this spec below) |
| **Why engineers own this** | Pure UI + validation work — no ML knowledge needed beyond the spec table |

### What It Adds

Expose additional hyperparameters for the existing Gradient Boosting algorithm that are currently hardcoded or not available in the UI.

### New Parameters to Expose

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_depth` | int | 6 | Maximum tree depth (currently exposed) |
| `min_child_samples` | int | 20 | Minimum samples in a leaf |
| `subsample` | float | 0.8 | Row sampling ratio per tree |
| `colsample_bytree` | float | 0.8 | Feature sampling ratio per tree |
| `reg_alpha` | float | 0.0 | L1 regularization |
| `reg_lambda` | float | 1.0 | L2 regularization |
| `min_split_gain` | float | 0.0 | Minimum gain to make a split |
| `scale_pos_weight` | float | auto | Class imbalance handling (auto = neg/pos ratio) |
| `num_leaves` | int | 31 | Max leaves per tree (LightGBM-specific) |
| `early_stopping_rounds` | int | 50 | Stop if no improvement for N rounds |

### Implementation

**C# side:**
- Add new fields to `Parameter` table entries for ML configuration
- Add form controls to Angular training configuration page (under "Advanced Parameters" collapsible section)
- Pass through to Python via existing JSON payload in training request

**Python side:**
- Read new params from request JSON
- Override defaults in `_train_lightgbm()` / `_train_xgboost()` / `_train_gbm()`
- Validate ranges (e.g., `subsample` must be 0-1, `max_depth` must be >0)

```python
# Parameter validation
HYPERPARAM_RANGES = {
    "max_depth": (1, 20),
    "min_child_samples": (1, 1000),
    "subsample": (0.1, 1.0),
    "colsample_bytree": (0.1, 1.0),
    "reg_alpha": (0.0, 100.0),
    "reg_lambda": (0.0, 100.0),
    "min_split_gain": (0.0, 10.0),
    "num_leaves": (2, 256),
    "early_stopping_rounds": (10, 500),
}

def validate_hyperparams(params: dict) -> dict:
    validated = {}
    for key, value in params.items():
        if key in HYPERPARAMS_RANGES:
            min_val, max_val = HYPERPARAMS_RANGES[key]
            validated[key] = max(min_val, min(max_val, value))
    return validated
```

### UI Consideration

Group under an **"Advanced Parameters"** collapsible section in the training config page. Show sensible defaults. Add tooltips explaining each parameter's impact on model behavior.

---

## Change #14: Backtesting — Model Validation Without Retraining

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Medium |
| **Effort** | 5-7 days |
| **Owner** | **Lead** (Python scoring engine + metrics) + Engineer A (Angular UI + CriteriaBuilder reuse) + Engineer B (BacktestController.cs + DB table + CSV upload API) |
| **Calendar** | Month 3, Week 11-12 |
| **Why all three** | This is the most cross-cutting feature in Tier 2. Lead handles the ML scoring logic (predict_proba, metrics computation, PSI). Engineer A builds the new Angular page (can reuse CriteriaBuilderComponent). Engineer B wires the C# controller + DB persistence. |
| **Knowledge transfer** | Lead explains: what backtesting means, why it doesn't retrain, what metrics to expect. 30-min session at start of Week 11. |

### What It Is

A new workflow that evaluates an **existing trained model** (pickle/ONNX) against a **new validation dataset** — without triggering a retrain. Exposed as a new Angular UI page called **"Backtesting"**.

### Problem With Current Workflow

Today, if a user wants to validate model performance on new data, they must:
1. Configure a new training run
2. Wait for full retrain (which also produces a new model they may not want)
3. Read metrics from the training results

This is confusing — users just want to answer: *"How does my existing model perform on different data?"*

### Data Source Options

| Source | How It Works |
|--------|-------------|
| **Read Replica (with Split Criteria)** | User defines criteria (date range, segment filters, etc.) via the same criteria builder used in training. System constructs a validation frame from the Read Replica. |
| **Uploaded CSV** | User uploads a labeled CSV file. System validates schema matches the model's expected features + label column. |

### Workflow

```
┌─── Backtesting Page (Angular UI) ───────────────────────────────────┐
│                                                                     │
│  Step 1: Select Model                                              │
│  ├─ Dropdown: existing trained models (from TrainingModelRun)      │
│  └─ Shows: algorithm, training date, AUC, status                   │
│                                                                     │
│  Step 2: Define Validation Data                                    │
│  ├─ Option A: Split Criteria (same criteria builder as training)   │
│  │   └─ Date range, account type, segment, etc.                   │
│  │   └─ Constructs validation frame from Read Replica              │
│  ├─ Option B: Upload CSV                                           │
│  │   └─ Validate schema (feature names + label column)            │
│  │   └─ Show preview (first 10 rows, row count, class balance)   │
│                                                                     │
│  Step 3: Run Backtest                                              │
│  ├─ Load existing model (pickle/ONNX, NO retrain)                  │
│  ├─ Apply same feature engineering pipeline as training             │
│  ├─ Score entire validation frame (predict_proba)                  │
│  └─ Compute metrics against labels                                 │
│                                                                     │
│  Step 4: Results                                                   │
│  ├─ AUC-ROC, AUC-PR, KS statistic                                 │
│  ├─ Precision-Recall at various thresholds                        │
│  ├─ Confusion matrix (at current risk bands)                      │
│  ├─ Score distribution (backtest vs training)                     │
│  ├─ PSI: backtest distribution vs training distribution           │
│  └─ Export results as PDF/CSV                                     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Implementation

#### Python Endpoint

```python
@app.route('/backtest', methods=['POST'])
def _backtest():
    """
    Validate existing model against new data without retraining.
    """
    request_data = request.get_json()
    model_id = request_data['modelId']
    data_source = request_data['dataSource']  # 'criteria' or 'csv'
    
    # Step 1: Load existing model (NO retrain)
    model_info = get_model_info(model_id)
    model = pickle.loads(model_info['model_blob'])
    
    # Step 2: Get validation data
    if data_source == 'criteria':
        criteria = request_data['criteria']
        df = fetch_data_from_criteria(criteria)  # Reuse CriteriaService logic
    elif data_source == 'csv':
        file_path = request_data['filePath']  # Uploaded CSV path
        df = pd.read_csv(file_path)
    
    # Step 3: Apply feature engineering (same pipeline as training)
    X, y = apply_feature_pipeline(df, model_info['feature_config'])
    
    # Step 4: Validate schema
    expected_features = model_info['feature_names']
    missing = set(expected_features) - set(X.columns)
    if missing:
        return jsonify({"error": f"Missing features: {list(missing)}"}), 400
    
    X = X[expected_features]  # Ensure column order matches
    
    # Step 5: Score (predict_proba)
    y_proba = model.predict_proba(X)[:, 1]
    
    # Step 6: Compute metrics
    from sklearn.metrics import roc_auc_score, average_precision_score
    results = {
        "auc_roc": roc_auc_score(y, y_proba),
        "auc_pr": average_precision_score(y, y_proba),
        "ks_statistic": compute_ks(y, y_proba),
        "score_distribution": compute_histogram(y_proba),
        "psi_vs_training": compute_psi(
            model_info['training_distribution'], 
            compute_histogram(y_proba)
        ),
        "confusion_matrix": compute_confusion_at_bands(
            y, y_proba, model_info['risk_bands']
        ),
        "n_records": len(X),
        "positive_rate": float(y.mean()),
    }
    
    return jsonify(results), 200
```

#### C# Side

- New `BacktestController.cs` with endpoint `POST /api/machineLearning/backtest`
- Calls Python service `/backtest` endpoint
- New `BacktestRun` table to store results for audit trail

#### Angular UI

- New route: `/machine-learning/backtesting`
- Reuse existing `CriteriaBuilderComponent` for Split Criteria option
- File upload component for CSV option
- Results page with charts (reuse PrimeNG chart components from monitoring)

### DB Schema

```sql
CREATE TABLE BacktestRun (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    DataSource NVARCHAR(20) NOT NULL,  -- 'criteria' or 'csv'
    CriteriaConfig NVARCHAR(MAX),      -- JSON (if criteria-based)
    FileName NVARCHAR(255),            -- Original filename (if CSV)
    RecordCount INT,
    PositiveRate DECIMAL(10,6),
    AucRoc DECIMAL(10,6),
    AucPr DECIMAL(10,6),
    KsStatistic DECIMAL(10,6),
    PsiVsTraining DECIMAL(10,6),
    ResultsJson NVARCHAR(MAX),         -- Full results (histogram, confusion matrix, etc.)
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    CreatedBy NVARCHAR(100),
    INDEX IX_BacktestRun_ModelId (ModelId, CreatedAt)
);
```

### Difference from Current Training Flow

| | Current Training | Backtesting (New) |
|---|---|---|
| **Triggers model retrain** | ✅ Yes | ❌ No |
| **Uses Split Criteria** | ✅ Yes (for train/test split) | ✅ Yes (for validation frame only) |
| **Data source** | Read Replica only | Read Replica OR uploaded CSV |
| **Output** | New model + metrics | Metrics only (against existing model) |
| **Purpose** | Build new model | Answer "how does existing model perform on new/different data?" |

### Use Cases

1. *"Model was trained 6 months ago — how is it performing on last month's data?"*
2. *"Client wants to see model performance on a different segment before going live"*
3. *"Regulator asks for model validation on a specific population"*
4. *"Compare champion model on new data without retraining a challenger"*

---

## Tier 2 Outcome

| Metric | After Tier 1 | After Tier 2 (ONNX) |
|--------|-------------|---------------------|
| P50 latency | ~150-200ms | **~5-10ms** |
| Throughput | ~150-200 RPS | ~500+ RPS |
| Training impact | Zero | Zero |
| Crash cascade | No | No (Python not in scoring) |
| Model staleness | 10 days (reduced) | Minutes (file watcher) |
| Max training rows | ~2-3M (OOM beyond) | **50M+** (chunked) |
| Algorithms | LightGBM, RF, GBM | + **XGBoost** |
| Hyperparameter control | Limited | **Full advanced params exposed** |

**Total effort: ~22-28 days (spread across 8 weeks / 2 months)**

**Testing Gate — End of Month 2 (Scoring Path)**:
- Lead benchmarks ONNX scoring: confirm <10ms P50
- Hot-reload test: replace .onnx file → confirm new model serves within 30s
- DataHub pagination: extract 200K+ rows without I/O pressure spike
- Regression: all Tier 1 improvements still hold

**Testing Gate — End of Month 3 (Full Tier 2)**:
- Train on 10M+ row dataset: confirm completes without OOM
- XGBoost model trains + exports to ONNX + scores correctly in C#
- Backtesting: validate known model on known data → metrics match expected values
- All Tier 1 regressions pass

> If choosing ONNX path (items 7+10), scoring latency drops to ~5-10ms.
