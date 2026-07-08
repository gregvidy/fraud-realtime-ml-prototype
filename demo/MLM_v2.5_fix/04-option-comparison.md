# Option Comparison — v2.5 Architecture Decisions

---

## Decision 1: FastAPI (Path A) vs ONNX in C# (Path B)

This is the key Tier 2 decision. Both paths are Windows-native and backward-compatible.

### Side-by-Side Comparison

| Factor | Path A: FastAPI + Uvicorn | Path B: ONNX Runtime in C# |
|--------|--------------------------|----------------------------|
| **Latency** | ~100-150ms (HTTP hop remains) | **~5-10ms** (no HTTP hop) |
| **Complexity** | Lower (Python-only change) | Medium (C# + Python changes) |
| **Dependencies** | Uvicorn, FastAPI | Microsoft.ML.OnnxRuntime NuGet |
| **Windows-native** | ✅ Uvicorn works | ✅ ONNX Runtime is Windows-first |
| **Python required for scoring** | Yes | **No** (Python only for training) |
| **Pickle risk** | Still present | **Eliminated** |
| **Custom algorithms** | Works as-is | Need fallback (HTTP to Python for `MainCustom`) |
| **Neural networks** | Works as-is | Need ONNX export for Keras models (extra work) |
| **Effort** | 3-5 days | 5-7 days |
| **Throughput** | ~200-300 RPS | **~500+ RPS** |

### Recommendation

**Path B (ONNX) for standard algorithms** (LightGBM, RF, GBM) — this is where 95% of clients are.

Keep Python HTTP fallback for custom algorithms and neural networks. This hybrid approach gives the best latency for the common case without breaking edge cases.

### Hybrid Implementation

Keep Python HTTP for custom/NN algorithms, use ONNX for everything else. See [02-tier2-performance-improvements.md](02-tier2-performance-improvements.md) → Change #7 for the full `ScoringService.cs` code.

---

## Decision 2: Waitress vs Gevent (Thread Pool)

This applies to Tier 1, Change #4 — increasing concurrency for the Flask/Gevent path.

### Comparison

| Factor | Waitress | Gevent (increased pool) |
|--------|----------|------------------------|
| **Architecture** | Thread-based WSGI | Green-thread (coroutine) WSGI |
| **Windows support** | ✅ Native, no issues | ✅ Works, but monkey-patching can be fragile |
| **Concurrency model** | Real OS threads | Cooperative green threads |
| **GIL impact** | Mitigated (LightGBM releases GIL) | Same (but I/O-bound code benefits) |
| **Complexity** | Drop-in replacement | Requires `monkey.patch_all()` |
| **Debugging** | Standard threading tools | Harder to debug cooperative scheduling |
| **Effort** | 0.5 day | 0.5 day |

### Recommendation

**Waitress** — simpler, more predictable on Windows, no monkey-patching.

```bash
waitress-serve --threads=8 --port=5555 MachineLearning:app
```

---

## Decision 3: Model Hot-Reload — FileSystemWatcher vs TTLCache

Applies to Tier 2, Change #8.

### Comparison

| Factor | FileSystemWatcher (C#) / watchdog (Python) | TTLCache (current) |
|--------|---------------------------------------------|-------------------|
| **Staleness** | Seconds (on file change) | Up to 10 days (864,000s TTL) |
| **Reliability** | Event-driven, near-instant | Time-based, no guarantee of freshness |
| **Complexity** | Low (C# built-in / Python `watchdog` already in codebase) | Already implemented |
| **Resource usage** | Minimal (OS-level file notification) | Minimal (in-memory cache) |
| **Edge cases** | May miss events on network drives | No edge cases |

### Recommendation

**FileSystemWatcher** as primary, **TTLCache as fallback** safety net.

---

## Decision 4: Score Logging Strategy

Applies to Tier 3 — how to log scores without impacting latency.

### Comparison

| Factor | Fire-and-Forget Async INSERT | In-Memory Buffer + Batch Flush | Sampling (1-in-N) |
|--------|------------------------------|-------------------------------|-------------------|
| **Added latency** | ~1-3ms | ~0ms (deferred) | ~0ms |
| **Data completeness** | 100% of scores logged | 100% of scores logged | Partial (e.g., 10%) |
| **Complexity** | Low | Medium (buffer management) | Low |
| **Risk** | Dropped INSERTs if DB is slow | Lost buffer on crash | Incomplete data for monitoring |
| **DB write volume** | 1 INSERT per score | Batch INSERT every 100ms | 1 INSERT per N scores |

### Recommendation

**In-memory buffer with batch flush** for production (best latency). Use **fire-and-forget** for simpler initial implementation, upgrade to buffer if latency impact is noticeable.

---

## Decision 5: Angular Charting Library

Applies to Tier 3, Component #6 — monitoring dashboard.

### Comparison

| Factor | PrimeNG `p-chart` | ngx-charts | Chart.js (direct) |
|--------|-------------------|------------|-------------------|
| **Angular 7 compatible** | ✅ Yes (v7.x of PrimeNG) | ✅ Yes | ✅ Yes (via wrapper) |
| **Already in project** | ✅ (if PrimeNG used elsewhere) | ❌ New dependency | ❌ New dependency |
| **Chart types** | Line, bar, pie, radar, etc. | Line, bar, pie, etc. | Full Chart.js library |
| **Customizability** | Medium (via Chart.js options) | High (D3-based) | High |
| **Bundle size** | Included with PrimeNG | ~150KB | ~60KB |

### Recommendation

**PrimeNG `p-chart`** — already part of the Angular ecosystem, wraps Chart.js, Angular 7 compatible. Avoid newer PrimeNG versions that require Angular 14+.

---

## Decision 6: Large Dataset Training — Chunked vs Ray vs Dask

Applies to Tier 2, Change #11 — training on >10M rows.

### Comparison

| Factor | Chunked → Parquet → LightGBM | Ray (Distributed) | Dask DataFrame | PySpark |
|--------|-------------------------------|-------------------|----------------|----------|
| **Max practical scale** | ~50M rows | 100M+ rows | ~50M rows | 1B+ rows |
| **Windows support** | ✅ Native | ❌ Experimental | 🟡 Works but fragile | ❌ Requires Hadoop/JVM |
| **New infrastructure** | None (local disk) | Cluster (head + workers) | None (single-node) | Cluster |
| **Memory efficiency** | ~500MB-1.5GB for 10M rows | Distributed across nodes | Similar to pandas (lazy eval) | Distributed |
| **Complexity** | Low — standard Python libs | High — Ray API, serialization, cluster mgmt | Medium — pandas-like but subtle differences | Very High |
| **Python version** | 3.8+ (current) | 3.9+ recommended | 3.8+ | 3.8+ |
| **Dependency footprint** | `pyarrow` (likely already present) | `ray[default]` (~200MB) | `dask[complete]` (~100MB) | `pyspark` + JDK |
| **LightGBM integration** | Native (`free_raw_data=True`) | `ray.train.lightgbm` (extra API) | `dask-lightgbm` (limited maintenance) | `synapse-ml` (Microsoft) |
| **Failure mode** | OOM if Parquet exceeds RAM on read | Network/serialization errors | Silent incorrect results on edge cases | JVM OOM, GC pauses |

### Recommendation

**Chunked → Parquet → LightGBM** for v2.5. Reasons:

1. Zero new infrastructure — works on existing Windows Server
2. No Python upgrade needed
3. 10-50M rows is well within single-machine capability with this approach
4. If clients need >50M rows in the future → that's a Track 2 (Linux/Docker) concern where Ray becomes viable

---

## Decision 7: XGBoost vs LightGBM — When to Use Which

Applies to Tier 2, Change #12 — adding XGBoost as algorithm option.

### Comparison

| Factor | LightGBM | XGBoost |
|--------|----------|----------|
| **Training speed** | Faster (leaf-wise growth) | Slower (level-wise default, but `grow_policy=lossguide` available) |
| **Accuracy (typical)** | ~Same | ~Same (marginal differences per dataset) |
| **Memory usage** | Lower (histogram-based, 8-bit bins) | Higher (32-bit default, `tree_method=hist` helps) |
| **Categorical handling** | Native (optimal split) | Requires encoding (one-hot or ordinal) |
| **Overfitting control** | `min_data_in_leaf`, `lambda_l1/l2` | `min_child_weight`, `alpha/lambda` |
| **ONNX export** | ✅ via `onnxmltools` | ✅ via `onnxmltools` (native support) |
| **GPU training** | ✅ `device=gpu` | ✅ `tree_method=gpu_hist` |
| **Community/maturity** | Large (Microsoft-backed) | Large (older, more papers reference it) |
| **Windows wheels** | ✅ pip install | ✅ pip install |
| **Existing client familiarity** | Current default — all clients use it | Commonly requested by data scientists |

### Recommendation

**Offer both** — let the user select during training configuration. They produce comparable results in most fraud scenarios. XGBoost gives clients with XGBoost-trained models elsewhere an easy migration path. LightGBM remains the default.

**Do NOT auto-select** based on dataset characteristics in v2.5 — that's a hyperparameter sweep feature for Track 2.

---

## Decision 8: Permutation Importance — When to Compute

Applies to Tier 3, Component #8.

### Comparison

| Timing | Pros | Cons | Recommended |
|--------|------|------|-------------|
| **At training time only** | Cheap (one-time cost), consistent baseline | Stale if data distribution changes | ✅ Yes (always) |
| **Weekly monitoring job** | Detects feature drift, shows evolving importance | Requires labeled data (may not always be available) | ✅ Yes (when labels available) |
| **At scoring time (per-prediction)** | Real-time relevance | Computationally infeasible (N features × N repeats × inference) | ❌ No |
| **On-demand (user-triggered)** | Flexible, no scheduled cost | Results not pre-computed for dashboards | 🟡 Nice-to-have |

### Recommendation

**Dual computation**:
1. Always compute at training time → store as model artifact (baseline)
2. Weekly Hangfire job recomputes on recent labeled data (if available) → compare rank correlation to baseline

If labels are not yet available for recent data, skip the monitoring recomputation — surface a "waiting for labels" status in the UI rather than computing on unlabeled data.

---

## Decision 9: Backtesting Data Source — Criteria Builder vs CSV Upload

Applies to Tier 2, Change #14.

### Comparison

| Factor | Read Replica (Split Criteria) | CSV Upload |
|--------|-------------------------------|------------|
| **Data freshness** | Live data from Read Replica | Point-in-time snapshot |
| **Schema validation** | Automatic (same pipeline as training) | Must validate against model's expected features |
| **Feature engineering** | Applied by system (same as training) | User must provide pre-engineered features |
| **Row count control** | Depends on criteria — could be 10 rows or 10M | User controls (file size limit) |
| **Audit trail** | Criteria stored as JSON — reproducible | File hash stored for reproducibility |
| **User effort** | Low (reuse familiar criteria builder) | Medium (prepare CSV, ensure schema matches) |
| **Offline / external data** | ❌ Not supported | ✅ Any labeled data source |
| **Regulatory use** | Good (tied to production data) | Good (can use regulator-provided test sets) |

### Recommendation

**Support both** — they serve different use cases:
- **Criteria Builder**: Day-to-day validation ("how's the model doing on last month's data?")
- **CSV Upload**: Regulatory validation and client-provided test datasets

For CSV: require **pre-engineered features** (column names must match model input). Provide a "Download Template" button that exports the expected schema.

---

## Decision 10: Explainability Method — SHAP vs PDP

Applies to Tier 3, Component #9.

### Comparison

| Factor | SHAP (TreeExplainer) | SHAP (DeepExplainer) | SHAP (KernelExplainer) | PDP | Built-in Importance |
|--------|---------------------|---------------------|----------------------|-----|---------------------|
| **Scope** | Local (per-prediction) | Local (per-prediction) | Local (per-prediction) | Global | Global |
| **Question answered** | "Why this score?" | "Why this score?" (NN) | "Why this score?" (any) | "How does feature X affect scores overall?" | "Which features are used most?" |
| **Computation cost** | ~100ms per prediction (tree models) | ~200-500ms per prediction (neural nets) | ~30s-2min per prediction | ~30s per model (training time) | Free |
| **Faithfulness** | ✅ Exact for tree models | ✅ DeepLIFT-based (faithful for NNs) | ✅ Exact Shapley values (model-agnostic) | 🟡 Marginal (ignores interactions) | 🟡 Biased toward high-cardinality |
| **Model-agnostic** | ❌ Tree-only | ❌ NN-only (TF/Keras/PyTorch) | ✅ Any model | ✅ Any model | ❌ Algorithm-specific |
| **Windows-compatible** | ✅ | ✅ | ✅ | ✅ | ✅ |

### Recommendation: Tiered SHAP Approach

| Layer | Method | When | Who Uses It |
|-------|--------|------|-------------|
| **Always** | Built-in + Permutation importance | Training time | Analysts, model reports |
| **Always** | PDP (top 10 features) | Training time | Stakeholders |
| **On-demand** | SHAP TreeExplainer | User-triggered or batch nightly | Investigators (tree-based models) |
| **On-demand** | SHAP DeepExplainer | User-triggered or batch nightly | Investigators (neural networks) |
| **Fallback** | SHAP KernelExplainer | On-demand (any other model) | Same as above |

**Do NOT compute SHAP at scoring time** — compute it after-the-fact when someone asks "why?".

---

## Decision 11: On-Demand Explainability — Sync vs Async

Applies to Tier 3, Component #9 — how to serve SHAP explanations.

### Comparison

| Factor | Synchronous | Asynchronous (submit + poll) |
|--------|-------------|------------------------------|
| **Latency** | ~100-500ms (tree models) | Near-instant submission, poll for result |
| **UX** | Simpler — click and see result | Loading state → result appears |
| **Timeout risk** | Possible on large models / custom algos | None |
| **Complexity** | Low | Medium (needs job queue + status polling) |

### Recommendation

**Synchronous for tree models** (TreeExplainer is fast: ~100ms). **Async for NN/custom models** (DeepSHAP ~200-500ms, KernelSHAP can take 30s-2min). Route based on `model.Algorithm`.
