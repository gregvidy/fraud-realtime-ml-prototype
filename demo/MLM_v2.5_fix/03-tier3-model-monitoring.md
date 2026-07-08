# Tier 3: Model Monitoring & Explainability — Month 4-5 (September–October 2026)

> **Client-Requested Feature — “How is the model performing? Is it degrading? When should we retrain?”**

## Team Assignment — Tier 3

### Month 4 (September) — Monitoring Infrastructure

| Component | Owner | Support | Calendar | Notes |
|-----------|-------|---------|----------|-------|
| 1. ModelScoreLog table | Engineer B | Lead (reviews schema) | Week 13 | DB migration — straightforward |
| 2. Score logging in ScoringService | Engineer A | Lead (reviews perf impact) | Week 13-14 | C# async INSERT — must not add latency |
| 3. ModelMonitoringSnapshot table | Engineer B | Lead (reviews schema) | Week 13 | DB migration — straightforward |
| 4. MonitoringService.cs (Hangfire) | Engineer A | **Lead** (PSI formula + logic review) | Week 15-16 | Lead teaches PSI concept in Week 13 |
| 5. REST API controller | Engineer B | Lead (review) | Week 15-16 | Standard CRUD — familiar pattern |
| 6. Angular monitoring page | Engineer A | Lead (chart requirements) | Week 15-16 | PrimeNG charts — Engineer A should be comfortable by now |
| 7. PSI threshold alert | **Lead** (logic) + Engineer A (UI banner) | — | Week 16 | Lead codes the threshold check, Engineer A wires the UI alert |

### Month 5 (October) — Explainability

| Component | Owner | Support | Calendar | Notes |
|-----------|-------|---------|----------|-------|
| 8. Permutation importance | **Lead** | Engineer B (DB column migration) | Week 17-18 | ML-specific: sklearn `permutation_importance` + Spearman drift |
| 9a. PDP (global) | **Lead** | Engineer A (Angular PDP charts) | Week 17-18 | ML-specific: `partial_dependence` at training time |
| 9b. SHAP (local, on-demand + batch) | **Lead** | Engineer B (API endpoints + ExplainabilityLog DB) | Week 19-20 | ML-specific: TreeExplainer / DeepExplainer / KernelExplainer, async job pattern |
| 9d. Explainability Angular UI | Engineer A | Lead (wireframe + requirements) | Week 17-20 | Tabs: Global, Local, Batch — charts + tables |
| 9e. Explainability API + DB | Engineer B | Lead (defines contracts) | Week 17-20 | REST endpoints + ExplainabilityLog table |

**Lead's role in Month 4**: Teaches PSI/drift concepts (1-hour session, Week 13). Codes PSI threshold logic. Reviews all monitoring PRs for correctness (e.g., histogram bucketing, async INSERT pattern).

**Lead's role in Month 5**: Owns ALL explainability ML code — permutation importance, PDP, SHAP (TreeExplainer, DeepExplainer, KernelExplainer). This is the most ML-heavy month. Engineers handle UI rendering and API plumbing only.

**Knowledge transfer sessions**:
- Week 13: "What is PSI and why it matters" (30 min) — for Engineers A & B
- Week 17: "Feature importance vs SHAP vs PDP" (1 hour) — so engineers understand what they’re rendering

---

## Monitoring Architecture (Windows-Native, Minimal New Infra)

```
┌─── Scoring Path ───────────────────────────────────────────────────┐
│                                                                     │
│  ScoringService.cs                                                 │
│  ├─ Score transaction                                              │
│  └─ Log score to DB ──► INSERT INTO ModelScoreLog                  │
│       (model_id, score, risk_band, timestamp, feature_hash)        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─── Monitoring Batch Job (Hangfire, daily/weekly) ──────────────────┐
│                                                                     │
│  MonitoringService.cs (new)                                        │
│  ├─ Query ModelScoreLog for last 7 days                            │
│  ├─ Compute:                                                       │
│  │   ├─ Score distribution (histogram buckets)                     │
│  │   ├─ PSI vs training distribution                               │
│  │   ├─ Volume (scores/day)                                        │
│  │   ├─ Latency percentiles (P50, P95, P99)                       │
│  │   └─ Risk band distribution                                    │
│  └─ Write to ModelMonitoringSnapshot table                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─── Angular UI ─────────────────────────────────────────────────────┐
│                                                                     │
│  New route: /machine-learning/monitoring                           │
│  ├─ Model selector dropdown                                       │
│  ├─ Score distribution chart (PrimeNG chart)                       │
│  ├─ PSI trend line (week-over-week)                                │
│  ├─ Volume trend line                                              │
│  ├─ Latency percentiles chart                                      │
│  ├─ Risk band breakdown (pie chart)                                │
│  └─ Alert banner: "PSI > 0.25 — consider retraining"              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Breakdown

### Component 1: `ModelScoreLog` Table

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Low |
| **Effort** | 0.5 day |
| **Owner** | Engineer B |
| **Calendar** | Month 4, Week 13 |
| **Prerequisite** | None — standard DB migration |

```sql
CREATE TABLE ModelScoreLog (
    Id BIGINT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    Score DECIMAL(10,6) NOT NULL,
    RiskBand NVARCHAR(20),
    LatencyMs INT,
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ModelScoreLog_ModelId_CreatedAt (ModelId, CreatedAt)
);
```

---

### Component 2: Score Logging in `ScoringService.cs`

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Medium |
| **Effort** | 2 days |
| **Owner** | Engineer A |
| **Calendar** | Month 4, Week 13-14 |
| **Prerequisite** | Lead reviews the async fire-and-forget pattern to ensure <1ms overhead |

After scoring, async INSERT to `ModelScoreLog`. Use fire-and-forget pattern or `BackgroundService` queue to avoid adding latency.

---

### Component 3: `ModelMonitoringSnapshot` Table

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Low |
| **Effort** | 0.5 day |
| **Owner** | Engineer B |
| **Calendar** | Month 4, Week 13 |
| **Prerequisite** | None — standard DB migration |

```sql
CREATE TABLE ModelMonitoringSnapshot (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    SnapshotDate DATE NOT NULL,
    ScoringVolume INT,
    PSI DECIMAL(10,6),
    P50LatencyMs INT,
    P95LatencyMs INT,
    P99LatencyMs INT,
    ScoreDistribution NVARCHAR(MAX),  -- JSON histogram
    RiskBandDistribution NVARCHAR(MAX), -- JSON
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ModelMonitoringSnapshot_ModelId (ModelId, SnapshotDate)
);
```

---

### Component 4: `MonitoringService.cs` (Hangfire Job)

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Medium |
| **Effort** | 3-4 days |
| **Owner** | Engineer A (C# service skeleton + Hangfire scheduling) + **Lead** (PSI computation logic) |
| **Calendar** | Month 4, Week 15-16 |
| **Knowledge transfer** | Lead explains PSI formula, histogram bucketing, and threshold interpretation in Week 13 session |

Query `ModelScoreLog`, compute stats, write snapshot.

**PSI Formula:**

$$PSI = \sum_{i} (p_i - q_i) \ln\frac{p_i}{q_i}$$

Where $p$ = current distribution, $q$ = training distribution.

---

### Component 5: REST API Controller

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Low |
| **Effort** | 1-2 days |
| **Owner** | Engineer B |
| **Calendar** | Month 4, Week 15-16 |
| **Prerequisite** | None — standard REST pattern, follows existing controller conventions |

`MachineLearningMonitoringController`:

- `GET /api/machineLearning/monitoring/{modelId}` — returns snapshots
- `GET /api/machineLearning/monitoring/{modelId}/scores` — returns recent scores

---

### Component 6: Angular Monitoring Page

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Medium |
| **Effort** | 3-5 days |
| **Owner** | Engineer A |
| **Calendar** | Month 4, Week 15-16 |
| **Prerequisite** | Lead provides wireframe/mockup of dashboard layout and chart requirements |

New component with PrimeNG charts:

- Score distribution histogram
- PSI trend line (week-over-week)
- Volume trend line
- Latency percentiles chart
- Risk band breakdown (pie chart)

---

### Component 7: PSI Threshold Alert

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Low |
| **Effort** | 1 day |
| **Owner** | **Lead** (threshold logic) + Engineer A (UI alert banner) |
| **Calendar** | Month 4, Week 16 |
| **Note** | Lead codes the threshold check in MonitoringService; Engineer A renders the banner in Angular |

If PSI > 0.25, flag in UI + optional email notification via existing `ParameterService`.

---

### Component 8: Permutation Importance (Training Artifact + Monitoring Signal)

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Medium |
| **Effort** | 2-3 days |
| **Owner** | **Lead** (Python implementation) + Engineer B (DB ALTER TABLE migration) |
| **Calendar** | Month 5, Week 17-18 |
| **Why Lead owns this** | sklearn `permutation_importance`, Spearman rank correlation, drift interpretation — all require ML/stats knowledge |

Permutation importance serves **two purposes**:

1. **Training artifact** — stored with the model at training time as a global feature ranking
2. **Monitoring signal** — recomputed periodically on recent data to detect feature relevance drift

#### At Training Time

Compute permutation importance on the validation set and store alongside the model:

```python
from sklearn.inspection import permutation_importance

# Compute after training, on validation set
perm_result = permutation_importance(
    model,
    X_val,
    y_val,
    n_repeats=10,
    scoring="average_precision",  # PR-AUC
    random_state=42,
    n_jobs=-1  # Parallelize across CPU cores
)

# Store top features with importance scores
feature_importance = pd.DataFrame({
    "feature": feature_cols,
    "importance_mean": perm_result.importances_mean,
    "importance_std": perm_result.importances_std
}).sort_values("importance_mean", ascending=False)

# Save as JSON artifact alongside model
importance_json = feature_importance.head(30).to_dict(orient="records")
# Store in TrainingModelRun.PermutationImportance (new column, NVARCHAR(MAX))
```

#### In Monitoring (Weekly Hangfire Job)

Recompute permutation importance on a sample of recent scored data (with labels, if available) and compare to training baseline:

```python
def compute_importance_drift(training_importance, current_importance, top_n=20):
    """
    Compare top-N feature rankings between training and current.
    Returns rank correlation (Spearman) — low correlation = feature drift.
    """
    from scipy.stats import spearmanr

    # Get top-N features from training
    training_top = training_importance.head(top_n)["feature"].tolist()
    current_ranked = current_importance.set_index("feature")

    training_ranks = list(range(top_n))
    current_ranks = [
        current_ranked.index.tolist().index(f) if f in current_ranked.index else top_n
        for f in training_top
    ]

    correlation, p_value = spearmanr(training_ranks, current_ranks)
    return {
        "spearman_correlation": correlation,
        "p_value": p_value,
        "alert": correlation < 0.7  # Threshold: significant rank change
    }
```

#### Storage

Extend existing tables:

```sql
-- Add to TrainingModelRun (training artifact)
ALTER TABLE TrainingModelRun
    ADD PermutationImportance NVARCHAR(MAX);  -- JSON array of {feature, importance_mean, importance_std}

-- Add to ModelMonitoringSnapshot (monitoring signal)
ALTER TABLE ModelMonitoringSnapshot
    ADD FeatureImportanceCorrelation DECIMAL(5,4),  -- Spearman rank correlation vs training
        TopFeaturesDrifted NVARCHAR(MAX);            -- JSON list of features that changed rank significantly
```

#### UI Integration

- **Training results page**: Show bar chart of top 20 features by permutation importance (already fits existing training summary UI)
- **Monitoring dashboard**: Add "Feature Importance Stability" card — shows Spearman correlation trend line. Alert if correlation drops below 0.7.

#### Comparison with Built-in Feature Importance

| Method | What It Measures | Bias | Cost |
|--------|-----------------|------|------|
| LightGBM `feature_importance("gain")` | Total split gain per feature | Biased toward high-cardinality features | Free (already available) |
| Permutation importance | Drop in metric when feature is shuffled | Unbiased, model-agnostic | Medium (~10× inference time on val set) |

**Both should be stored** — built-in importance is free and useful for quick reference. Permutation importance is the trustworthy signal for monitoring and reporting.

---

### Component 9: Model Explainability (SHAP + PDP + Feature Importance)

| Attribute | Detail |
|-----------|--------|
| **Complexity** | Medium-High |
| **Effort** | 4-6 days |
| **Owner** | **Lead** (all ML logic: SHAP, PDP, batch job) + Engineer A (Angular UI: 3 tabs) + Engineer B (API endpoints + ExplainabilityLog DB) |
| **Calendar** | Month 5, Week 17-20 (full month) |
| **Why Lead owns ML logic** | SHAP TreeExplainer, DeepExplainer, KernelExplainer, PDP — none of these can be delegated without ML background. Engineers render the output (charts, tables) which is standard frontend/backend work. |
| **Knowledge transfer** | Lead runs 1-hour session in Week 17: "What SHAP values mean, how to read a waterfall chart, PDP interpretation" — so engineers understand what they’re building UI for |

A tiered approach to model explanation — different methods for different questions, computed at different times.

#### Explainability Tier Map

| Question | Method | When Computed | Cost | Scope |
|----------|--------|--------------|------|-------|
| "Which features matter most overall?" | Permutation Importance | Training time | Low-Medium | Global |
| "Which features matter most overall?" | LightGBM built-in importance | Training time | Free | Global |
| "How does feature X affect score in general?" | PDP (Partial Dependence Plots) | Training time | Medium | Global |
| "How does feature X affect this specific prediction?" | ICE (Individual Conditional Expectation) | On-demand | Medium | Local |
| "Why did this specific transaction score 0.87?" | SHAP TreeExplainer | On-demand / Batch | Medium-High | Local (tree-based models) |
| "Why did this specific transaction score 0.87?" | SHAP DeepExplainer | On-demand / Batch | Medium | Local (neural networks) |
| "Why did this specific transaction score 0.87?" | SHAP KernelExplainer | On-demand | Medium-High | Local (any other model) |

#### A. Global Explanations (Computed at Training Time)

**Stored as model artifacts — computed once, displayed always.**

##### Partial Dependence Plots (PDP)

```python
from sklearn.inspection import PartialDependenceDisplay
import matplotlib.pyplot as plt
import json

def compute_pdp(model, X_val, feature_cols, top_n=10):
    """
    Compute PDP for top-N most important features.
    Store as JSON arrays for frontend rendering.
    """
    # Get top features from permutation importance (already computed)
    top_features = get_top_features(model, X_val, feature_cols, n=top_n)
    
    pdp_results = {}
    for feature in top_features:
        # Compute partial dependence
        from sklearn.inspection import partial_dependence
        pd_result = partial_dependence(
            model, X_val, features=[feature],
            kind="average",  # PDP (average across all samples)
            grid_resolution=50
        )
        
        pdp_results[feature] = {
            "grid_values": pd_result["grid_values"][0].tolist(),
            "average_prediction": pd_result["average"][0].tolist()
        }
    
    return pdp_results  # Store as JSON in TrainingModelRun.PdpResults
```

##### Storage

```sql
-- Extend TrainingModelRun
ALTER TABLE TrainingModelRun
    ADD PdpResults NVARCHAR(MAX),          -- JSON: {feature: {grid_values, average_prediction}}
        GlobalExplainability NVARCHAR(MAX); -- JSON: combined global explanation summary
```

##### UI (Training Results Page)

- **Feature Importance bar chart** (permutation importance — already in Component 8)
- **PDP line charts**: For top 10 features, show how the average prediction changes as feature value changes
- Interactive: click a feature in the importance chart → show its PDP below

#### B. Local Explanations (On-Demand / Batch)

**Not computed at scoring time (too expensive). Computed on-demand or in batch.**

##### SHAP Explainer Selection (TreeExplainer → DeepExplainer → KernelExplainer)

```python
import shap

def explain_prediction(model, X_instance, feature_names, model_type="lightgbm"):
    """
    Explain a single prediction using SHAP.
    Called on-demand (user clicks "Explain" on a specific score).
    Uses a 3-tier explainer hierarchy:
      1. TreeExplainer  — exact, fast (tree-based models)
      2. DeepExplainer  — DeepLIFT-based, fast (neural networks)
      3. KernelExplainer — model-agnostic, slower (everything else)
    """
    if model_type in ["lightgbm", "xgboost", "random_forest", "gbm"]:
        # TreeExplainer: exact SHAP values for tree models (fast)
        explainer = shap.TreeExplainer(model)
    elif model_type in ["neural_network", "deep_learning", "keras", "tensorflow", "pytorch"]:
        # DeepExplainer: DeepLIFT + Shapley for neural networks (fast, NN-native)
        background = shap.sample(X_background, 100)
        explainer = shap.DeepExplainer(model, background)
    else:
        # KernelExplainer: model-agnostic fallback (slower, 30s-2min per prediction)
        # Use a background sample for efficiency
        background = shap.sample(X_background, 100)
        explainer = shap.KernelExplainer(model.predict_proba, background)
    
    shap_values = explainer.shap_values(X_instance)
    
    # Format for frontend
    explanation = {
        "base_value": float(explainer.expected_value[1]),  # Expected positive class prob
        "prediction": float(model.predict_proba(X_instance)[0][1]),
        "features": [
            {
                "name": feature_names[i],
                "value": float(X_instance.iloc[0, i]),
                "shap_value": float(shap_values[1][0][i]),  # Contribution to positive class
                "direction": "increases_risk" if shap_values[1][0][i] > 0 else "decreases_risk"
            }
            for i in range(len(feature_names))
        ]
    }
    
    # Sort by absolute SHAP value (most impactful first)
    explanation["features"].sort(key=lambda x: abs(x["shap_value"]), reverse=True)
    
    return explanation
```

##### Batch SHAP (Nightly Job — Top Riskiest Predictions)

```python
def batch_explain_top_risky(model, recent_scores_df, top_n=100):
    """
    Nightly Hangfire job: explain the top-N riskiest predictions from today.
    Stores results for review without manual triggering.
    """
    # Get top N highest-scoring transactions
    top_risky = recent_scores_df.nlargest(top_n, "score")
    
    explainer = shap.TreeExplainer(model)
    X_top = top_risky[feature_cols]
    
    shap_values = explainer.shap_values(X_top)
    
    explanations = []
    for idx in range(len(X_top)):
        explanations.append({
            "transaction_id": top_risky.iloc[idx]["transaction_id"],
            "score": float(top_risky.iloc[idx]["score"]),
            "top_drivers": get_top_drivers(shap_values, X_top, idx, n=5)
        })
    
    return explanations  # Store in ExplainabilityLog table
```

#### API Endpoints

```
POST /api/machineLearning/explain
Body: { modelId: 123, transactionData: {...} }
Returns: SHAP explanation for single prediction

GET /api/machineLearning/explain/batch/{modelId}?date=2026-05-18
Returns: Pre-computed batch explanations for top risky predictions

GET /api/machineLearning/explain/global/{modelId}
Returns: PDP results + permutation importance (training artifacts)
```

#### DB Schema

```sql
CREATE TABLE ExplainabilityLog (
    Id BIGINT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    TransactionId NVARCHAR(100),
    Score DECIMAL(10,6),
    ExplanationMethod NVARCHAR(20),  -- 'tree_shap', 'deep_shap', 'kernel_shap', 'batch_shap'
    ExplanationJson NVARCHAR(MAX),   -- Full SHAP output
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ExplainabilityLog_ModelId (ModelId, CreatedAt)
);
```

#### Angular UI — Explainability Page

```
┌─── Explainability Page (/machine-learning/explainability) ────────┐
│                                                                     │
│  Tab 1: Global Explanations                                       │
│  ├─ Feature Importance bar chart (permutation importance)          │
│  ├─ PDP charts for top 10 features                                │
│  └─ Feature correlation heatmap (optional)                        │
│                                                                     │
│  Tab 2: Local Explanations (On-Demand)                            │
│  ├─ Input: paste transaction JSON or select from recent scores     │
│  ├─ Output: waterfall chart (SHAP)                                │
│  │   └─ Base value → feature contributions → final score           │
│  └─ Top drivers table: feature name, value, contribution           │
│                                                                     │
│  Tab 3: Batch Explanations (Pre-Computed)                         │
│  ├─ Date selector                                                  │
│  ├─ Table: top 100 riskiest predictions with top 5 drivers each   │
│  └─ Click row → full waterfall chart                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### Computation Cost & Risks

See [05-caveats-and-risks.md](05-caveats-and-risks.md) → Tier 3 → `Model Explainability` for full risk assessment including SHAP blocking, KernelSHAP latency, DeepSHAP backend dependency, and Angular rendering concerns.

---

## Tier 3 Outcome

| Metric | After Tier 2 (ONNX) | After Tier 3 |
|--------|---------------------|-------------|
| P50 latency | ~5-10ms | ~6-12ms (+1ms logging) |
| Throughput | ~500+ RPS | ~450+ RPS |
| Monitoring | None | ✅ Score dist, PSI, latency |
| Feature importance | Built-in only (biased) | ✅ Permutation importance (unbiased) + drift tracking |
| Explainability | None | ✅ Global (PDP) + Local (SHAP) + Batch (nightly top risky) |

**Total effort: ~18-25 days (spread across 8 weeks / 2 months)**

**Testing Gate — End of Month 4 (Monitoring)**:
- Score logging adds <1ms overhead (benchmarked under load)
- PSI computes correctly against known distributions (unit test with synthetic data)
- Dashboard renders with real scored data (not just mocks)
- Alert fires when PSI > 0.25 on synthetic drift scenario
- All Tier 1 + Tier 2 regressions still pass

**Testing Gate — End of Month 5 (Explainability)**:
- Permutation importance stored after training (verify JSON in DB)
- PDP computed for top 10 features, renders correctly in Angular
- On-demand SHAP returns explanation in <5s for single prediction
- Batch SHAP job completes nightly for top 100 predictions
- DeepSHAP returns valid explanations for a neural network model (test with dummy Keras model)
- KernelSHAP returns valid explanations for a custom model (test with dummy custom model)
- All previous tier regressions pass
