# Caveats & Risks — v2.5 MLM Fixation

---

## Tier 1 Caveats

### `sys.exit()` Removal

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Some `sys.exit()` calls may be in error paths that depend on process termination | Child processes may continue in broken state | Ensure all error paths return proper HTTP responses (400/500) and clean up resources before returning |
| Downstream C# handlers may not expect error JSON responses | Unhandled error formats in `PythonService.cs` | Verify all callers handle `{"error": msg}` responses — currently they timeout, so any response is an improvement |

### Service Split (Scoring + Training)

| Risk | Impact | Mitigation |
|------|--------|-----------|
| MSI installer complexity doubles | Installer may fail or misconfigure | Test MSI on clean Windows Server; document service dependencies |
| Port conflicts between two services | Service startup failures | Use distinct ports (e.g., 5555 for scoring, 5556 for training) and configure via environment variables |
| Shared state between scoring and training | Race conditions or stale data | Identify all shared state (model cache, DB connections) — ensure each service has its own isolated state |

---

## Tier 2 Caveats

### ONNX Export Compatibility

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Not all sklearn preprocessing is ONNX-exportable | Training succeeds but ONNX export fails | Validate ONNX export in training pipeline; keep Python HTTP fallback for unsupported cases |
| Custom feature transformations may not have ONNX converters | Missing operators at runtime | Use `skl2onnx` custom converters or move feature preprocessing to C# |
| ONNX model size may differ from pickle size | Memory pressure on C# service | Profile ONNX model sizes; set memory limits appropriately |

### Neural Network / Custom Algorithm Fallback

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Keras/TensorFlow ONNX export requires extra work | ~5% of clients on neural networks can't use fast path | Keep Python HTTP fallback for these algorithms; ONNX export for Keras is a separate workstream |
| `MainCustom` algorithm bypass | Custom algorithms can't be exported | Python HTTP fallback handles this; document which algorithms use which path |

### DataHub Paginated Extraction

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Pagination adds total training time | Training takes longer (more round trips) | Sleep 2s between batches is a guideline — tune based on DataHub load |
| Data consistency during paginated read | Rows may change between pages | Use snapshot isolation or read from replica; ORDER BY ensures deterministic pagination |

---

## Tier 3 Caveats

### Score Logging Adds Latency

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Each score INSERT adds ~1-3ms to the hot path | Scoring latency increases by 1-3ms | Use fire-and-forget async INSERT or in-memory buffer that flushes every 100ms. Don't `await` the DB write. |

### `ModelScoreLog` Table Grows Fast

| Risk | Impact | Mitigation |
|------|--------|-----------|
| At 100 RPS, that's **8.6M rows/day** | DB storage and query performance degrade | Add retention policy: Hangfire job purges logs > 30 days. Or sample (log 1 in 10 scores). |
| Index bloat on high-volume tables | INSERT performance degrades over time | Partition by month; archive old partitions; use appropriate index strategy |

### PSI Needs Training Distribution as Baseline

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Current training doesn't store score distribution | PSI can't be computed without baseline | v2.5 training must save score histogram to `TrainingModelRun.Distributions` (partially exists — extend it). |
| Baseline distribution changes after retraining | PSI resets, historical trend breaks | Store each training run's distribution; allow selecting which baseline to compare against |

### Angular 7 Charting Limitations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| PrimeNG charts work on Angular 7, but some newer chart components don't | Missing chart types or broken UI | Use `p-chart` (wrapper around Chart.js) which is Angular 7 compatible. Avoid newer PrimeNG versions that require Angular 14+. |

### No Ground Truth Labels in Real-Time

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Can't compute AUC/recall in monitoring — labels come weeks/months later | No true performance metrics in real-time | Monitor **proxy metrics**: PSI, volume, latency, score distribution shifts. True model performance (AUC) tracked only after label feedback loop. |

### Feature Drift Detection

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Requires logging individual feature values per score — much more data | Massive storage and performance impact | **Defer to Track 2**. For v2.5, only log score + metadata. Feature-level drift is a modernization feature. |

---

## Cross-Cutting Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| **Regression in existing clients** | Medium | High | Full regression test suite on OCBC, FAB, ING environments before rollout |
| **MSI packaging complexity** | Medium | Medium | Test installer on clean Windows Server VM; automate MSI build in CI |
| **C# ↔ Python interface changes** | Low | High | Changes are additive (new ONNX path); existing Python HTTP path unchanged |
| **Performance gains not realized** | Low | High | Benchmark each tier independently; measure on realistic data volumes |
| **Team capacity** | Medium | High | Tiers are independent — can deliver Tier 1 alone and defer Tier 2/3 if needed |
| **Client-specific edge cases** | Medium | Medium | Each client has different feature sets and data volumes; test with representative datasets |
---

## Tier 2 Caveats (continued)

### Large Dataset Training (>10M Rows)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Parquet temp file consumes disk space | At 10M rows × 50 features, Parquet file is ~2-4 GB on disk | Ensure training server has sufficient disk; clean up temp files after training completes |
| `to_pandas()` after Parquet read still requires RAM | Full DataFrame materializes in memory before passing to LightGBM | Use `free_raw_data=True` on LightGBM Dataset to release pandas memory immediately. For >50M rows, use LightGBM binary format (avoids pandas entirely). |
| PyArrow version compatibility | Older PyArrow may not support all Parquet features | Pin PyArrow >=8.0; test on existing Python environment |
| Chunked DB reads may timeout on slow replicas | Long-running queries against Read Replica | Set query timeout generously (30min+); implement retry per chunk; log progress per chunk so restarts can resume from last successful chunk |
| Feature engineering must be applied per-chunk consistently | Risk of inconsistent transformations across chunks | Apply feature engineering AFTER full Parquet file is assembled, not per-chunk. Chunks are raw data only. |

### XGBoost Algorithm Support

| Risk | Impact | Mitigation |
|------|--------|------------|
| XGBoost + LightGBM dependency conflicts | Both install `libgomp` / OpenMP — potential DLL conflicts on Windows | Test side-by-side installation; pin versions that are known compatible |
| XGBoost `DMatrix` API differs from LightGBM `Dataset` | Code paths diverge — more maintenance | Abstract behind a common `ModelTrainer` interface; keep algorithm-specific code isolated |
| XGBoost ONNX export may not support all configurations | Certain params (e.g., custom objectives) may fail ONNX conversion | Validate ONNX export as part of training pipeline; fall back to Python HTTP scoring for unsupported configs |
| Client expectation of identical results to their local XGBoost | Version differences or preprocessing mismatches | Document exact XGBoost version; ensure feature preprocessing is identical to what client provides |
| `scale_pos_weight` auto-calculation | Different imbalance ratios per client — wrong auto value could hurt | Let user override; default auto-calculation uses training set class ratio; display computed value in UI for transparency |

### Additional GBM Hyperparameters

| Risk | Impact | Mitigation |
|------|--------|------------|
| Users set extreme values (e.g., `max_depth=100`) | Model overfits severely, training takes hours | Enforce validation ranges (server-side); show warnings in UI for extreme values |
| Too many options overwhelm non-technical users | Confusion, wrong parameter choices | Hide behind "Advanced Parameters" collapsible section; provide presets ("Balanced", "Anti-overfit", "Fast training") |
| Backward compatibility — existing models trained without new params | Old models may fail if code assumes new params exist | Default all new params to current implicit values; null/missing = use default |

---

## Tier 3 Caveats (continued)

### Permutation Importance

| Risk | Impact | Mitigation |
|------|--------|------------|
| Computationally expensive on large validation sets | At 50 features × 10 repeats × 100K rows = 500 inference passes | Subsample validation set (10K rows is sufficient for stable importance estimates); parallelize with `n_jobs=-1` |
| Correlated features get underestimated importance | If features A and B are correlated, shuffling A still leaves B as proxy — both appear less important | Document this limitation; consider grouped permutation importance in Track 2 |
| Monitoring recomputation requires labeled data | Labels arrive weeks/months after scoring | Only recompute when new labeled data is available; show "last computed" date in UI; don't alert on stale importance |
| Rank correlation threshold (0.7) may be too sensitive or too loose | False alerts or missed drift | Make threshold configurable per model; start conservative (0.6) and tune based on client feedback |
| Storage overhead for importance JSON | 30 features × 3 fields = ~1KB per model — negligible | Negligible. No mitigation needed. |
| Importance differences between `gain` (built-in) and permutation confuse users | "Why does the built-in say feature X is #1 but permutation says #5?" | Explain in UI tooltip: built-in measures split gain (biased toward high-cardinality), permutation measures actual predictive contribution (unbiased). Show both side-by-side with explanation. |

### Backtesting (Validation Without Retraining)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Feature engineering mismatch between training and backtesting | Model receives differently transformed features → misleading metrics | Reuse the exact same feature pipeline code path. For CSV uploads, require pre-engineered features matching model's expected schema. |
| CSV upload with wrong schema silently produces garbage scores | Metrics look valid but are meaningless | Strict schema validation: column names, types, and count must match exactly. Reject with clear error listing missing/extra columns. |
| Large backtest dataset (>1M rows) causes timeout | Request timeout, process killed | Run backtesting as async job (same pattern as training — `multiprocessing.Process`). Show progress bar. |
| Class imbalance in uploaded CSV skews metrics | AUC looks good but precision/recall is misleading | Display class balance prominently in results ("Positive rate: 0.3% — highly imbalanced"). Show PR-AUC alongside ROC-AUC. |
| Users confuse backtesting with retraining | "I ran a backtest, why didn't my model update?" | Clear UI messaging: "Backtesting evaluates your existing model — no new model is created." Separate navigation from training page. |
| PSI computation requires training distribution baseline | If training didn't store score histogram, PSI can't be computed | Enforce that training stores score distribution (from Tier 3 monitoring prerequisite). If missing for old models, show "N/A — retrain to enable PSI comparison". |
| CSV file upload security | Malicious files, path traversal, oversized uploads | Validate file extension (.csv only), enforce max file size (500MB), sanitize filename, store in temp directory with UUID name, delete after processing. |
| Audit trail for regulatory backtests | Regulator asks "show me you validated on date X" | Store all backtest runs in `BacktestRun` table with full config, timestamp, and user who triggered it. Never delete. |

### Model Explainability (SHAP + PDP)

| Risk | Impact | Mitigation |
|------|--------|------------|
| SHAP `shap` package is large (~50MB) and has complex dependencies | Installation conflicts with existing packages (numpy, scipy versions) | Pin `shap` version compatible with existing numpy/scipy. Test full dependency resolution before deployment. |
| SHAP TreeExplainer produces different results than KernelExplainer for same model | Inconsistency confuses users comparing explanations | Only use TreeExplainer for tree models. Document that methods may give slightly different attributions. Never mix methods for the same model type. |
| PDP ignores feature interactions | PDP shows feature X increases risk, but only when combined with feature Y | Document limitation in UI. Add ICE plots in Track 2 for interaction-aware local explanations. |
| On-demand SHAP blocks Python service | If many users request explanations simultaneously, scoring service degrades | Run explainability in the **training** Python service (not scoring service). Separate worker pool. |
| SHAP waterfall chart rendering on Angular 7 | SHAP's built-in plotting uses matplotlib (server-side) — need to recreate in Angular | Generate chart data as JSON arrays (base value, feature contributions). Render as horizontal bar chart in PrimeNG. Don't send matplotlib images. |
| LIME explanations can be unstable | Same input with different random seeds gives different top features | Set fixed random seed. Average over multiple runs (3-5) for stability. Document that LIME is an approximation. |
| Batch SHAP job on 100 predictions daily | Adds ~10s compute + DB writes per model per day | Acceptable cost. Schedule during off-peak hours. Only run for models with >100 scores/day. |
| Users over-interpret single SHAP values | "Feature X has SHAP 0.05 — it must be causing fraud!" | Add disclaimer in UI: "SHAP values show feature contribution to this specific prediction, not causal relationship." Provide documentation link. |
| Custom objective functions break SHAP TreeExplainer | Non-standard loss functions may not be supported | Detect at explanation time. Fall back to LIME with warning: "Exact SHAP not available for custom objectives — using approximate method." |