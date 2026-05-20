# Phase 3 — Feature Platform & Training at Scale

> **Duration**: 6-8 weeks
> **Merged from**: Phase 4 (Feature DSL/Registry) + Phase 5 (Distributed Training)
> **Goal**: Unified feature governance, distributed training on 100M+ rows, multi-model sweeps
> **Prerequisite**: Phase 2 complete (streaming pipeline, ScyllaDB operational)

---

## 3.1 What This Phase Delivers

By the end of Phase 3, FraudML has:
- **Feature Registry (Feast-lite DSL)**: YAML-defined features linked to batch (dbt), streaming (ksqlDB), and request-time sources
- **Training-serving consistency**: Features defined once, computed identically for training and serving
- **Distributed training via Ray**: Train on 100M+ rows across multiple workers
- **Multi-model sweeps**: Train LightGBM + XGBoost + RF in a single config-driven run
- **Config-driven experiments**: YAML experiment definitions, fully reproducible via MLflow

---

## 3.2 Architecture After Phase 3

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 3 ARCHITECTURE                                 │
│                                                                              │
│  ┌─ Feature Registry (Feast-lite) ──────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  feature_definitions/                                                │   │
│  │  ├─ entities.yaml          (user, device, merchant)                  │   │
│  │  ├─ user_features.yaml     (26 features: batch + streaming)          │   │
│  │  ├─ device_features.yaml   (7 features)                              │   │
│  │  ├─ merchant_features.yaml (5 features)                              │   │
│  │  └─ request_features.yaml  (3 features: amount, is_intl, local_hour)│   │
│  │                                                                      │   │
│  │  Links to:                                                            │   │
│  │  ├─ dbt models (batch computation)                                   │   │
│  │  ├─ ksqlDB queries (streaming computation)                           │   │
│  │  └─ Request payload fields (request-time)                            │   │
│  │                                                                      │   │
│  │  Serves:                                                              │   │
│  │  ├─ Scoring: feature vector assembly (ordered by registry)           │   │
│  │  ├─ Training: dataset builder (point-in-time joins from registry)    │   │
│  │  └─ Monitoring: expected distributions per feature (from registry)   │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Training Plane (Ray Cluster) ───────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  ┌─ Ray Head ──────────────────────────────────────────────────────┐ │   │
│  │  │                                                                  │ │   │
│  │  │  train_model.py (config-driven)                                  │ │   │
│  │  │  ├─ Read experiment YAML                                         │ │   │
│  │  │  ├─ Build dataset from ClickHouse (streaming read, chunked)      │ │   │
│  │  │  ├─ Distribute to Ray workers                                    │ │   │
│  │  │  ├─ Each worker: train 1 model+hyperparam combo                  │ │   │
│  │  │  ├─ Collect results → MLflow                                     │ │   │
│  │  │  └─ Select champion → promote                                    │ │   │
│  │  │                                                                  │ │   │
│  │  └──────────────────────────────────────────────────────────────────┘ │   │
│  │                                                                      │   │
│  │  ┌─ Ray Worker 1 ──┐  ┌─ Ray Worker 2 ──┐  ┌─ Ray Worker N ──┐    │   │
│  │  │ LightGBM train  │  │ XGBoost train   │  │ RF train        │    │   │
│  │  │ n_est=3000      │  │ n_est=1000      │  │ n_est=500       │    │   │
│  │  │ leaves=127      │  │ depth=6         │  │ depth=20        │    │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘    │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3.3 Work Breakdown

### 3.3.1 Feature Registry (Feast-lite DSL)

| Task | Description | Days |
|------|-------------|------|
| **YAML DSL design** | Define feature definition schema (see below). Support: batch, streaming, request-time modes. | 2 |
| **Feature registry service** | Python module that loads YAML definitions. Provides: `get_feature_vector(entity_ids)`, `get_training_features(model_id)`, `get_feature_metadata(feature_name)`. | 4 |
| **Link to dbt models** | Each batch feature references a dbt model + column. Registry validates that dbt model exists. | 2 |
| **Link to ksqlDB** | Each streaming feature references a ksqlDB table. Registry validates connectivity. | 2 |
| **Link to request payload** | Request-time features mapped to JSON path in scoring request. | 0.5 |
| **Feature versioning** | Git-tracked YAML files. Version bumps on schema changes. Backward-compatible loading. | 1 |
| **Training dataset builder** | Build training dataset from registry: query ClickHouse for batch features + join historical streaming features. Point-in-time correct. | 5 |
| **Scoring integration** | Scoring service reads feature order from registry. Assembles vector in registry-defined order. | 2 |
| **CLI tool** | `fraudml features list`, `fraudml features validate`, `fraudml features describe <name>`. | 2 |

**Subtotal: ~20.5 days**

#### Feature Definition YAML Schema

```yaml
# feature_definitions/entities.yaml
entities:
  - name: user
    join_key: user_id
    description: "Bank customer / account holder"

  - name: device
    join_key: device_id
    description: "Device used for transaction"

  - name: merchant
    join_key: merchant_id
    description: "Payment recipient"
```

```yaml
# feature_definitions/user_features.yaml
feature_group:
  name: user_features
  entity: user
  description: "User-level features for fraud scoring"
  version: 1

features:
  # --- Batch features (from dbt → ClickHouse → ScyllaDB/Redis) ---
  - name: user_account_age_days
    dtype: int
    mode: batch
    source:
      dbt_model: fct_user_features
      column: account_age_days
    description: "Days since account creation"
    default: 0

  - name: user_txn_count_7d
    dtype: int
    mode: batch
    source:
      dbt_model: int_user_txn_stats
      column: txn_count_7d
    window: 7d
    description: "Transaction count in last 7 days"
    default: 0
    monitoring:
      expected_range: [0, 500]
      drift_threshold_psi: 0.25

  - name: user_txn_amount_30d
    dtype: float
    mode: batch
    source:
      dbt_model: int_user_txn_stats
      column: txn_amount_30d
    window: 30d
    description: "Total transaction amount in last 30 days"
    default: 0.0

  # --- Streaming features (from ksqlDB → Kafka → Redis/ScyllaDB) ---
  - name: user_txn_count_5m
    dtype: int
    mode: streaming
    source:
      ksqldb_table: user_txn_count_5m
      column: txn_count_5m
    window: 5m
    description: "Transaction count in last 5 minutes"
    default: 0
    monitoring:
      expected_range: [0, 20]
      drift_threshold_psi: 0.30

  - name: user_txn_amount_5m
    dtype: float
    mode: streaming
    source:
      ksqldb_table: user_txn_count_5m
      column: txn_amount_5m
    window: 5m
    description: "Total transaction amount in last 5 minutes"
    default: 0.0

  - name: user_distinct_merchants_1h
    dtype: int
    mode: streaming
    source:
      ksqldb_table: user_txn_count_1h
      column: distinct_merchants_1h
    window: 1h
    description: "Distinct merchants in last 1 hour"
    default: 0

  - name: user_failed_logins_15m
    dtype: int
    mode: streaming
    source:
      ksqldb_table: user_failed_logins_15m
      column: failed_logins_15m
    window: 15m
    description: "Failed login attempts in last 15 minutes"
    default: 0
```

```yaml
# feature_definitions/request_features.yaml
feature_group:
  name: request_features
  entity: null  # no entity join — comes from request payload
  description: "Transaction-level features extracted from scoring request"
  version: 1

features:
  - name: txn_amount
    dtype: float
    mode: request
    source:
      json_path: "$.amount"
    description: "Transaction amount"
    default: 0.0

  - name: is_international
    dtype: bool
    mode: request
    source:
      json_path: "$.is_international"
    description: "Whether transaction crosses borders"
    default: false

  - name: local_hour
    dtype: int
    mode: request
    source:
      json_path: "$.local_hour"
    description: "Local hour of transaction (0-23)"
    default: 12
```

#### How the Registry Powers Consistency

```
                      Feature Registry (YAML)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ┌─────────┐   ┌──────────┐   ┌──────────────┐
         │ Scoring  │   │ Training │   │ Monitoring   │
         │ Service  │   │ Pipeline │   │ Service      │
         └────┬─────┘   └────┬─────┘   └──────┬───────┘
              │              │                 │
    "Give me features   "Build dataset    "Expected
     for user u_042      with these 41     distribution
     in THIS order"      features from     for each
              │          ClickHouse"        feature"
              │              │                 │
              ▼              ▼                 ▼
         Same 41 features, same order, same defaults
```

### 3.3.2 Distributed Training with Ray

| Task | Description | Days |
|------|-------------|------|
| **Ray cluster setup** | Ray head + N workers in Docker Compose. Head: 4 CPU, 8 GB. Workers: 4 CPU, 8 GB each. | 2 |
| **ClickHouse streaming reader** | Read 100M+ rows from ClickHouse without loading into memory. Use `clickhouse-connect` streaming with `query_arrow` (Apache Arrow batches). | 3 |
| **LightGBM on Ray** | `ray.train.lightgbm.LightGBMTrainer` for distributed LightGBM. Data partitioned across workers via Ray Data. | 3 |
| **XGBoost on Ray** | `ray.train.xgboost.XGBoostTrainer` (native Ray integration). | 2 |
| **RandomForest on Ray** | `joblib` backend swap to Ray (`ray.util.joblib`). RF parallelism across Ray workers. | 2 |
| **Multi-model sweep** | YAML config defines multiple model types + hyperparameter grids. Ray Tune dispatches all combos in parallel. | 4 |
| **Temporal (OOT) split** | Split by time: 80% training, 20% OOT validation. Built into dataset builder from registry. | 1 |
| **Calibration pipeline** | After base model training: isotonic/sigmoid calibration on holdout. Extract calibration arrays for serving. | 2 |
| **MLflow integration** | Each Ray trial logs to MLflow: params, metrics, artifacts (model files, calibration, feature importance). | 2 |
| **Champion selection** | After sweep: rank all models by PR-AUC (or configurable metric). Best model auto-promoted to MLflow Production stage. | 2 |
| **100M benchmark** | Generate 100M synthetic rows. Run full pipeline: build dataset + train + evaluate. Measure time and resources. | 3 |

**Subtotal: ~26 days**

#### Experiment Config (Multi-Model Sweep)

```yaml
# training/experiments/full_sweep_v1.yaml
experiment:
  name: "ocbc_fraud_sweep_2026Q2"
  description: "Full model sweep for OCBC quarterly retrain"

dataset:
  source: clickhouse
  query: "SELECT * FROM fraudml.fct_training_dataset"
  feature_registry: "feature_definitions/"  # use registry for feature list
  streaming_features: true  # include streaming features from ksqlDB history

split:
  method: temporal
  test_size: 0.20
  temporal_column: event_timestamp

calibration:
  method: isotonic
  fraction: 0.20

models:
  - type: lightgbm
    grid:
      n_estimators: [1000, 3000, 5000]
      num_leaves: [63, 127, 255]
      learning_rate: [0.01, 0.05]
      min_child_samples: [10, 20]
      reg_alpha: [0.0, 0.1]
      reg_lambda: [1.0, 5.0]
    early_stopping_rounds: 100

  - type: xgboost
    grid:
      n_estimators: [1000, 3000]
      max_depth: [4, 6, 8]
      learning_rate: [0.01, 0.05]
      subsample: [0.8, 1.0]
      colsample_bytree: [0.8, 1.0]
    early_stopping_rounds: 100

  - type: random_forest
    grid:
      n_estimators: [500, 1000]
      max_depth: [10, 20, null]
      min_samples_split: [2, 5]
      max_features: ["sqrt", "log2"]

selection:
  metric: pr_auc                    # select champion by PR-AUC
  secondary_metric: roc_auc         # tiebreaker
  min_recall: 0.80                  # must achieve 80% recall
  min_precision_at_recall: 0.05     # at target recall

output:
  model_name: "ocbc_fraud_model"
  auto_promote: true                # auto-promote if better than current champion
  notify: ["analytics-team@gbgplc.com"]

ray:
  num_workers: 4
  cpus_per_worker: 4
  gpus_per_worker: 0
  max_concurrent_trials: 8
```

#### Training at Scale — Memory Strategy

```
100M rows × 41 features × 8 bytes = ~32 GB (full dataset in memory)

Strategy: DON'T load it all at once.

┌─────────────────────────────────────────────────────────────────┐
│  ClickHouse (100M rows)                                        │
│       │                                                         │
│       ▼ query_arrow(chunk_size=1_000_000)                       │
│  Apache Arrow batches (1M rows each, ~320 MB)                  │
│       │                                                         │
│       ▼ ray.data.from_arrow()                                   │
│  Ray Dataset (100 partitions, distributed across workers)       │
│       │                                                         │
│       ├─► Worker 1: partitions 1-25 → LightGBM.fit(data_slice) │
│       ├─► Worker 2: partitions 26-50                            │
│       ├─► Worker 3: partitions 51-75                            │
│       └─► Worker 4: partitions 76-100                           │
│                                                                 │
│  Each worker: ~8 GB RAM (25M rows × 41 features × 8 bytes)    │
│  Total cluster: 4 workers × 8 GB = 32 GB distributed          │
│  vs. single-node: 32 GB on one machine ← OOM on most servers  │
└─────────────────────────────────────────────────────────────────┘
```

```python
# training/train_distributed.py
import ray
from ray import train
from ray.train.lightgbm import LightGBMTrainer
from ray.train import ScalingConfig
import clickhouse_connect

def build_ray_dataset(config: dict) -> ray.data.Dataset:
    """Stream 100M+ rows from ClickHouse into Ray Dataset."""
    client = clickhouse_connect.get_client(host="clickhouse", port=8123)
    
    # Stream as Arrow batches (1M rows each, ~320 MB per batch)
    arrow_batches = client.query_arrow_stream(
        config["dataset"]["query"],
        settings={"max_block_size": 1_000_000}
    )
    
    # Convert to Ray Dataset (auto-distributes across cluster)
    ds = ray.data.from_arrow(arrow_batches)
    
    # Temporal split
    split_ts = ds.aggregate(
        ray.data.aggregate.Quantile("event_timestamp", q=0.80)
    )
    train_ds = ds.filter(lambda row: row["event_timestamp"] <= split_ts)
    test_ds = ds.filter(lambda row: row["event_timestamp"] > split_ts)
    
    return train_ds, test_ds

def train_lightgbm_distributed(config: dict):
    """Train LightGBM on Ray cluster."""
    train_ds, test_ds = build_ray_dataset(config)
    
    trainer = LightGBMTrainer(
        label_column="is_fraud",
        params=config["models"][0]["params"],
        scaling_config=ScalingConfig(
            num_workers=config["ray"]["num_workers"],
            resources_per_worker={
                "CPU": config["ray"]["cpus_per_worker"]
            }
        ),
        datasets={"train": train_ds, "valid": test_ds},
    )
    
    result = trainer.fit()
    return result
```

---

## 3.4 Training-Serving Consistency Deep Dive

The most critical correctness requirement: **features seen during training must match features seen during scoring**.

| Feature Mode | Training Source | Serving Source | Consistency Mechanism |
|-------------|---------------|---------------|----------------------|
| **Batch** | ClickHouse (dbt model) | ScyllaDB / Redis (Feast materialized) | Same dbt SQL computes both. Feast materializes from dbt output. |
| **Streaming** | ClickHouse (historical ksqlDB output, backfilled) | Redis / ScyllaDB (live ksqlDB output) | ksqlDB query logic is canonical. Historical backfill replays Kafka topics through same queries. |
| **Request-time** | ClickHouse (raw transaction column) | HTTP request JSON field | Feature registry maps both to same semantic field. |

#### Consistency Validation

```python
# training/validate_consistency.py
def validate_feature_consistency(registry, sample_size=1000):
    """Sample N entities, compare training features vs serving features."""
    
    entities = sample_entities(sample_size)
    mismatches = []
    
    for entity in entities:
        # What training would compute
        training_features = registry.compute_training_features(
            entity.id, 
            as_of_timestamp=entity.latest_txn_time
        )
        
        # What serving currently has
        serving_features = registry.fetch_serving_features(entity.id)
        
        for feature_name in registry.get_feature_names():
            train_val = training_features.get(feature_name)
            serve_val = serving_features.get(feature_name)
            
            if not approximately_equal(train_val, serve_val, rtol=0.01):
                mismatches.append({
                    "entity": entity.id,
                    "feature": feature_name,
                    "training_value": train_val,
                    "serving_value": serve_val
                })
    
    if mismatches:
        logger.warning(f"Training-serving skew detected: {len(mismatches)} mismatches")
    
    return mismatches
```

---

## 3.5 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | Feature registry YAML definitions (41 features) | `fraudml features validate` passes |
| 2 | Feature registry powers scoring (feature vector assembly) | Score response uses registry-ordered features |
| 3 | Feature registry powers training (dataset builder) | Training dataset has same 41 columns in same order |
| 4 | Ray cluster operational | `ray status` shows head + N workers |
| 5 | Distributed LightGBM training on 10M rows | Completes in < 10 minutes on 4 workers |
| 6 | Multi-model sweep (LightGBM + XGBoost + RF) | All models logged to MLflow, champion selected |
| 7 | 100M row benchmark | Training dataset build + model training completes |
| 8 | Training-serving consistency validation | `validate_consistency.py` reports < 1% skew |
| 9 | YAML experiment config → full pipeline | Single `make train CONFIG=sweep.yaml` runs everything |

---

## 3.6 Demo Checkpoint (D3)

### What to Show

1. **Feature registry walkthrough**: Show YAML definitions, run `fraudml features list`
2. **Multi-model sweep**: Kick off sweep with 3 model types × hyperparameter grid → MLflow shows all runs
3. **MLflow comparison**: Side-by-side comparison of LightGBM vs XGBoost vs RF (ROC curves, PR curves, feature importance)
4. **100M row training**: Show ClickHouse streaming read → Ray distributed training → completion
5. **Champion auto-selection**: Best model auto-promoted, scoring service hot-reloads

### Benchmark Targets

| Metric | Target |
|--------|--------|
| 10M row training (LightGBM, single model) | < 10 minutes |
| 100M row training (LightGBM, single model) | < 60 minutes |
| Multi-model sweep (12 combos) | < 2 hours |
| Feature consistency validation | < 1% skew |
| Dataset build from ClickHouse (100M rows) | < 15 minutes |

---

## 3.7 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Ray adds infrastructure complexity | Harder to deploy on-prem | Ray can run in "local mode" (single node) for small datasets. Only enable cluster mode for 100M+. |
| ClickHouse Arrow streaming memory | Large Arrow batches consume RAM | Tune `max_block_size` to 500K-1M. Monitor worker RSS. |
| Feature registry becomes stale | Definitions drift from actual computation | CI/CD: `fraudml features validate` in pipeline. Blocks deploy if dbt model or ksqlDB query is missing. |
| Multi-model sweep takes too long | Blocks training pipeline for hours | Limit concurrent trials. Use early stopping aggressively. Cancel unpromising trials via Ray Tune scheduler (ASHAScheduler). |
| Historical streaming features unavailable | Training dataset missing real-time features for old transactions | Backfill: replay Kafka topics through ksqlDB for historical period. Or: use dbt to compute approximate streaming features from batch data (same window logic in SQL). |
