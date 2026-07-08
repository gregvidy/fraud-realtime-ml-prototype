# Phase 4 — ML Lifecycle & Monitoring

> **Duration**: 4-6 weeks
> **Merged from**: Phase 6 (Model Lifecycle & Champion/Challenger) + Phase 7 (Monitoring & Drift)
> **Goal**: DataRobot-like ML lifecycle — champion/challenger, canary deployment, automated drift detection
> **Prerequisite**: Phase 3 complete (feature registry, MLflow, distributed training)

---

## 4.1 What This Phase Delivers

By the end of Phase 4, FraudML has:
- **Model states**: Champion, Challenger, Shadow, Retired — with automated transitions
- **Canary deployment**: Route X% of traffic to challenger for shadow scoring
- **A/B comparison**: Automated statistical comparison of champion vs challenger
- **Drift detection**: Feature drift (PSI) and prediction drift via Evidently
- **Auto-retrain triggers**: When drift exceeds threshold, trigger training pipeline
- **Management Portal integration**: Monitoring dashboards in Grafana + alerts

---

## 4.2 Architecture After Phase 4

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 4 ARCHITECTURE                                 │
│                                                                              │
│  ┌─ Scoring Service ────────────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  POST /score                                                         │   │
│  │  ├─ Load champion model (always)                                     │   │
│  │  ├─ If canary enabled AND random() < canary_pct:                     │   │
│  │  │   ├─ Also load challenger model                                   │   │
│  │  │   ├─ Score with BOTH models                                       │   │
│  │  │   ├─ Return champion score to client                              │   │
│  │  │   └─ Log both scores (champion + challenger) for comparison       │   │
│  │  └─ Log: score, features, model_version, latency → PostgreSQL        │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Model Lifecycle Service ────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  Model States:                                                        │   │
│  │  ┌──────────┐    promote    ┌───────────┐   retire   ┌──────────┐   │   │
│  │  │ STAGING  │──────────────►│ CHAMPION  │───────────►│ RETIRED  │   │   │
│  │  └──────────┘               └─────┬─────┘            └──────────┘   │   │
│  │       │                           │                                  │   │
│  │       │ enable canary             │ if challenger wins               │   │
│  │       ▼                           ▼                                  │   │
│  │  ┌──────────┐    compare    ┌───────────┐                           │   │
│  │  │ SHADOW   │──────────────►│ CHALLENGER│                           │   │
│  │  │ (silent) │  (after N     │ (canary)  │                           │   │
│  │  └──────────┘   days)       └───────────┘                           │   │
│  │                                                                      │   │
│  │  Transitions:                                                         │   │
│  │  ├─ training → STAGING (auto, after training completes)              │   │
│  │  ├─ STAGING → SHADOW (manual or auto if canary_auto=true)            │   │
│  │  ├─ SHADOW → CHALLENGER (auto after shadow period + metrics check)   │   │
│  │  ├─ CHALLENGER → CHAMPION (auto if challenger outperforms)           │   │
│  │  ├─ CHAMPION → RETIRED (auto when new champion promoted)            │   │
│  │  └─ Any → RETIRED (manual rollback)                                  │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─ Monitoring Service ─────────────────────────────────────────────────┐   │
│  │                                                                      │   │
│  │  ┌─ Evidently Reports (scheduled) ──────────────────────────────┐   │   │
│  │  │  Daily:                                                       │   │   │
│  │  │  ├─ Prediction drift (score distribution vs training baseline) │   │   │
│  │  │  ├─ Feature drift (PSI per feature vs training distribution)  │   │   │
│  │  │  └─ Data quality (nulls, out-of-range, new categories)        │   │   │
│  │  │  Weekly:                                                      │   │   │
│  │  │  ├─ Champion vs Challenger comparison report                  │   │   │
│  │  │  └─ Model performance (if labeled data available)             │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  │  ┌─ Alerts ─────────────────────────────────────────────────────┐   │   │
│  │  │  ├─ PSI > 0.25 on any feature → WARNING                      │   │   │
│  │  │  ├─ PSI > 0.50 on any feature → CRITICAL (auto-retrain?)    │   │   │
│  │  │  ├─ Score distribution shift > 20% → WARNING                 │   │   │
│  │  │  ├─ Challenger outperforms champion by > 5% PR-AUC → NOTIFY │   │   │
│  │  │  └─ Model age > 90 days → WARNING (staleness)               │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  │  Output: Grafana dashboards + Evidently HTML reports                 │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4.3 Work Breakdown

### 4.3.1 Model Lifecycle Management

| Task | Description | Days |
|------|-------------|------|
| **Model state machine** | Implement state transitions: STAGING → SHADOW → CHALLENGER → CHAMPION → RETIRED. Store state in PostgreSQL `model_registry` table. | 3 |
| **MLflow integration** | Map model states to MLflow stages: Staging, Production, Archived. Sync transitions bidirectionally. | 2 |
| **Model version API** | REST endpoints: `GET /models`, `POST /models/{id}/promote`, `POST /models/{id}/rollback`, `GET /models/{id}/history`. | 2 |
| **Canary routing in scoring** | Scoring service loads champion + challenger. Shadow-scores challenger on X% of requests. Always returns champion score. | 3 |
| **Canary configuration** | Per-tenant: `canary_enabled`, `canary_percentage` (0-100), `canary_duration_days`, `canary_auto_promote`. Stored in PostgreSQL. | 1 |
| **A/B comparison** | After canary period: compare champion vs challenger on overlapping scored transactions. Statistical test (Mann-Whitney U or bootstrap). | 3 |
| **Auto-promote logic** | If challenger PR-AUC > champion PR-AUC by threshold AND no regression on secondary metrics → auto-promote. Notify team. | 2 |
| **Rollback mechanism** | `POST /models/{id}/rollback` → load previous champion from MLflow artifacts → hot-reload scoring. | 1 |
| **Model metadata** | Store per model: training config (YAML), feature list (from registry), calibration profile, training metrics, deployment timestamp. | 1 |

**Subtotal: ~18 days**

#### Model State Machine

```python
# services/model_lifecycle.py
from enum import Enum
from datetime import datetime, timedelta

class ModelState(Enum):
    STAGING = "staging"           # Just trained, not yet deployed
    SHADOW = "shadow"             # Silently scoring (no traffic impact)
    CHALLENGER = "challenger"     # Canary scoring (X% traffic)
    CHAMPION = "champion"         # Production model (serves all traffic)
    RETIRED = "retired"           # Archived, no longer serving

VALID_TRANSITIONS = {
    ModelState.STAGING:     [ModelState.SHADOW, ModelState.RETIRED],
    ModelState.SHADOW:      [ModelState.CHALLENGER, ModelState.RETIRED],
    ModelState.CHALLENGER:  [ModelState.CHAMPION, ModelState.RETIRED],
    ModelState.CHAMPION:    [ModelState.RETIRED],
    ModelState.RETIRED:     [ModelState.CHAMPION],  # rollback
}

class ModelLifecycleService:
    def __init__(self, db, mlflow_client, scoring_service, notifier):
        self.db = db
        self.mlflow = mlflow_client
        self.scoring = scoring_service
        self.notifier = notifier
    
    async def promote(self, model_id: str, target_state: ModelState):
        model = await self.db.get_model(model_id)
        
        if target_state not in VALID_TRANSITIONS[model.state]:
            raise InvalidTransition(
                f"Cannot transition from {model.state} to {target_state}"
            )
        
        if target_state == ModelState.CHAMPION:
            # Retire current champion
            current_champion = await self.db.get_champion(model.tenant_id)
            if current_champion:
                await self._transition(current_champion, ModelState.RETIRED)
            
            # Promote new champion
            await self._transition(model, ModelState.CHAMPION)
            
            # Hot-reload scoring service
            await self.scoring.reload_model(model.tenant_id, model.artifact_path)
            
            # Sync to MLflow
            self.mlflow.transition_model_version_stage(
                model.mlflow_name, model.mlflow_version, "Production"
            )
            
            await self.notifier.send(
                f"Model {model.name} v{model.version} promoted to CHAMPION "
                f"for tenant {model.tenant_id}"
            )
    
    async def evaluate_canary(self, tenant_id: str):
        """Called after canary period ends. Compare champion vs challenger."""
        champion = await self.db.get_champion(tenant_id)
        challenger = await self.db.get_challenger(tenant_id)
        
        if not challenger:
            return
        
        # Get overlapping scores from score_log
        comparison = await self.db.get_canary_comparison(
            tenant_id, champion.id, challenger.id,
            since=challenger.canary_start
        )
        
        if comparison.challenger_pr_auc > comparison.champion_pr_auc * 1.05:
            # Challenger is 5%+ better
            if challenger.canary_auto_promote:
                await self.promote(challenger.id, ModelState.CHAMPION)
            else:
                await self.notifier.send(
                    f"Challenger outperforms champion by "
                    f"{comparison.improvement_pct:.1f}% — review for promotion"
                )
```

#### Canary Scoring Implementation

```python
# serving/scoring.py
@app.post("/score")
async def score(req: ScoreRequest):
    tenant = get_tenant(req)
    champion = model_registry.get_champion(tenant.id)
    
    # Always score with champion
    features = await feature_service.fetch(req)
    champion_score = await predict(champion, features)
    
    # Canary: shadow-score with challenger
    challenger = model_registry.get_challenger(tenant.id)
    if challenger and random.random() < tenant.canary_percentage / 100:
        try:
            challenger_score = await predict(challenger, features)
            # Log both for comparison (non-blocking)
            asyncio.create_task(log_canary_scores(
                req.transaction_id, tenant.id,
                champion.id, champion_score,
                challenger.id, challenger_score,
                features
            ))
        except Exception as e:
            logger.warning(f"Challenger scoring failed: {e}")
            # Challenger failure doesn't affect response
    
    # Always return champion result
    return ScoreResponse(
        score=champion_score.calibrated_probability,
        risk_band=champion_score.risk_band,
        is_flagged=champion_score.is_flagged,
        model_version=champion.version
    )
```

### 4.3.2 Monitoring & Drift Detection (Evidently)

| Task | Description | Days |
|------|-------------|------|
| **Score logging** | Every scored transaction: log `(txn_id, model_id, score, risk_band, features_hash, latency_ms, timestamp)` to PostgreSQL. Async batch INSERT (non-blocking). | 2 |
| **Feature logging** | Log full feature vector for sampled transactions (1 in 10) to support feature drift analysis. | 1 |
| **Evidently integration** | Install Evidently. Build report generators: DataDriftPreset, TargetDriftPreset, DataQualityPreset. | 3 |
| **Daily drift job** | Hangfire/Celery Beat: run Evidently daily. Compare last 24h features vs training reference dataset. Output: JSON metrics + HTML report. | 3 |
| **PSI computation** | Per-feature PSI (Population Stability Index). Store in `monitoring_snapshots` table. | 2 |
| **Grafana monitoring dashboards** | (1) Score distribution over time, (2) Per-feature PSI heatmap, (3) Scoring volume/latency, (4) Model version timeline, (5) Canary comparison. | 4 |
| **Alert rules** | Prometheus alerting rules → AlertManager → webhook/email. Thresholds: PSI > 0.25 (warning), PSI > 0.50 (critical), score shift > 20%. | 2 |
| **Auto-retrain trigger** | When PSI > critical threshold on ≥ 3 features → optionally trigger training pipeline via API call. Requires human approval by default. | 2 |
| **Evidently HTML reports** | Store HTML reports in object storage (or PostgreSQL BYTEA). Accessible via Management Portal or direct URL. | 1 |
| **Model staleness alert** | Alert if champion model age > configurable threshold (default 90 days). | 0.5 |

**Subtotal: ~20.5 days**

#### Evidently Drift Report

```python
# monitoring/drift_detector.py
from evidently import ColumnMapping
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset, DataQualityPreset
from evidently.metrics import (
    DatasetDriftMetric,
    DataDriftTable,
    ColumnDriftMetric,
)
import json
from datetime import datetime, timedelta

class DriftDetector:
    def __init__(self, db, feature_registry, score_log_repo, notifier):
        self.db = db
        self.registry = feature_registry
        self.score_log = score_log_repo
        self.notifier = notifier
    
    async def run_daily_report(self, tenant_id: str, model_id: str):
        """Generate drift report comparing last 24h vs training baseline."""
        
        # 1. Get training reference distribution
        reference_data = await self.db.get_training_reference(model_id)
        
        # 2. Get last 24h production data
        current_data = await self.score_log.get_recent_features(
            tenant_id, 
            since=datetime.utcnow() - timedelta(days=1)
        )
        
        if len(current_data) < 100:
            logger.info(f"Insufficient data for drift analysis ({len(current_data)} rows)")
            return
        
        # 3. Build Evidently report
        feature_cols = self.registry.get_feature_names()
        
        column_mapping = ColumnMapping(
            prediction="score",
            numerical_features=[f for f in feature_cols if self.registry.get_dtype(f) in ("int", "float")],
            categorical_features=[f for f in feature_cols if self.registry.get_dtype(f) in ("bool", "str")]
        )
        
        report = Report(metrics=[
            DatasetDriftMetric(),
            DataDriftTable(),
            DataQualityPreset(),
        ])
        
        report.run(
            reference_data=reference_data,
            current_data=current_data,
            column_mapping=column_mapping
        )
        
        # 4. Extract PSI per feature
        report_json = json.loads(report.json())
        drift_results = self._extract_psi(report_json)
        
        # 5. Store snapshot
        await self.db.save_monitoring_snapshot({
            "tenant_id": tenant_id,
            "model_id": model_id,
            "snapshot_date": datetime.utcnow().date(),
            "drift_results": drift_results,
            "scoring_volume": len(current_data),
            "dataset_drift_detected": drift_results["dataset_drift"],
        })
        
        # 6. Save HTML report
        html_report = report.get_html()
        await self.db.save_drift_report_html(tenant_id, model_id, html_report)
        
        # 7. Check alerts
        critical_features = [
            f for f, psi in drift_results["feature_psi"].items()
            if psi > 0.50
        ]
        warning_features = [
            f for f, psi in drift_results["feature_psi"].items()
            if 0.25 < psi <= 0.50
        ]
        
        if len(critical_features) >= 3:
            await self.notifier.send_alert(
                level="critical",
                message=f"CRITICAL DRIFT: {len(critical_features)} features exceed PSI 0.50: "
                        f"{critical_features}. Consider retraining.",
                tenant_id=tenant_id,
                model_id=model_id
            )
        elif warning_features:
            await self.notifier.send_alert(
                level="warning",
                message=f"DRIFT WARNING: {len(warning_features)} features exceed PSI 0.25: "
                        f"{warning_features}.",
                tenant_id=tenant_id,
                model_id=model_id
            )
```

#### Grafana Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FraudML Monitoring — {Tenant Name} — {Model Name}                        │
│                                                                             │
│  ┌─ Score Distribution (7-day rolling) ──────┐  ┌─ PSI Trend ───────────┐ │
│  │                                            │  │                       │ │
│  │  ▓▓▓▓▓▓▓▓                                │  │  ───────────────────── │ │
│  │  ▓▓▓▓▓▓▓▓▓▓▓                             │  │       PSI = 0.12      │ │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓                          │  │  ─ ─ ─ ─ ─ ─ ─ ─ ─  │ │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓                       │  │       threshold: 0.25│ │
│  │  0.0  0.2  0.4  0.6  0.8  1.0             │  │  Mon Tue Wed Thu Fri │ │
│  │  Today ── Training baseline ─ ─            │  │                       │ │
│  └────────────────────────────────────────────┘  └───────────────────────┘ │
│                                                                             │
│  ┌─ Feature PSI Heatmap ────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  user_txn_count_7d     ░░░░░░░░░░░░░░░░░░░░░░░ 0.08                 │  │
│  │  user_txn_amount_30d   ░░░░░░░░░░░░░░░░░░░░░░░░░░ 0.12             │  │
│  │  user_txn_count_5m     ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 0.28 ⚠️     │  │
│  │  device_txn_count_1h   ░░░░░░░░░░░░░░░ 0.05                         │  │
│  │  merchant_fraud_rate   ░░░░░░░░░░░░░░░░░░ 0.09                      │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Scoring Performance ─────────────┐  ┌─ Model Timeline ──────────────┐ │
│  │  TPS: 1,847    P50: 12ms          │  │  v3 ████████████████ CHAMPION  │ │
│  │  Errors: 0%    P95: 34ms          │  │  v4 ░░░░░░░░ CHALLENGER (15%) │ │
│  │  Volume: 142K/day  P99: 78ms      │  │  v2 ─────── RETIRED           │ │
│  └────────────────────────────────────┘  └───────────────────────────────┘ │
│                                                                             │
│  ┌─ Champion vs Challenger Comparison ──────────────────────────────────┐  │
│  │                                                                       │  │
│  │  Metric          Champion (v3)   Challenger (v4)   Δ                  │  │
│  │  PR-AUC          0.412           0.438             +6.3% ✅           │  │
│  │  ROC-AUC         0.934           0.941             +0.7%              │  │
│  │  Recall@0.05P    0.823           0.847             +2.9%              │  │
│  │  Brier Score     0.031           0.028             -9.7% ✅           │  │
│  │  Avg Latency     11ms            13ms              +18% ⚠️           │  │
│  │                                                                       │  │
│  │  Canary period: 7/14 days    Auto-promote: enabled                   │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4.4 DB Schema Additions

```sql
-- Model registry (lifecycle states)
CREATE TABLE model_registry (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    model_version INT NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'staging',  -- staging/shadow/challenger/champion/retired
    mlflow_run_id VARCHAR(50),
    mlflow_model_version INT,
    artifact_path VARCHAR(500),
    training_config JSONB,             -- full YAML experiment config
    feature_list JSONB,                -- ordered feature names from registry
    calibration_profile JSONB,         -- isotonic x/y arrays
    metrics JSONB,                     -- pr_auc, roc_auc, brier, recall, etc.
    canary_percentage INT DEFAULT 0,
    canary_start TIMESTAMP,
    canary_auto_promote BOOLEAN DEFAULT false,
    promoted_at TIMESTAMP,
    retired_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(tenant_id, model_name, model_version)
);

-- Score log (per scored transaction)
CREATE TABLE score_log (
    id BIGSERIAL,
    tenant_id VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    model_id INT NOT NULL,
    score DECIMAL(10,6) NOT NULL,
    risk_band VARCHAR(20),
    is_flagged BOOLEAN,
    latency_ms INT,
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Partitioned by month for retention
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Canary score log (dual scores)
CREATE TABLE canary_score_log (
    id BIGSERIAL,
    tenant_id VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    champion_model_id INT NOT NULL,
    champion_score DECIMAL(10,6),
    challenger_model_id INT NOT NULL,
    challenger_score DECIMAL(10,6),
    features_hash VARCHAR(64),        -- SHA256 of feature vector (consistency check)
    created_at TIMESTAMP DEFAULT NOW(),
    
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Feature log (sampled, for drift analysis)
CREATE TABLE feature_log (
    id BIGSERIAL,
    tenant_id VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(100),
    model_id INT NOT NULL,
    features JSONB NOT NULL,           -- full feature vector
    created_at TIMESTAMP DEFAULT NOW(),
    
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Monitoring snapshots (daily)
CREATE TABLE monitoring_snapshot (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    model_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    scoring_volume INT,
    dataset_drift_detected BOOLEAN,
    feature_psi JSONB,                 -- {feature_name: psi_value}
    score_distribution JSONB,          -- histogram buckets
    p50_latency_ms INT,
    p95_latency_ms INT,
    p99_latency_ms INT,
    report_html_path VARCHAR(500),     -- path to Evidently HTML report
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(tenant_id, model_id, snapshot_date)
);

-- Create monthly partitions (retention: 6 months)
CREATE TABLE score_log_2026_05 PARTITION OF score_log
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE score_log_2026_06 PARTITION OF score_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
-- ... auto-create via pg_partman or cron
```

---

## 4.5 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | Model state machine (STAGING → SHADOW → CHALLENGER → CHAMPION → RETIRED) | API: promote, rollback, state history |
| 2 | Canary routing in scoring service | Score same txn with both models; log shows champion + challenger scores |
| 3 | A/B comparison after canary period | Comparison report: PR-AUC delta, statistical significance |
| 4 | Auto-promote (if configured) | Challenger outperforms → auto-promoted → scoring reloads |
| 5 | One-click rollback | `POST /models/{id}/rollback` → previous champion restored in < 30s |
| 6 | Evidently drift reports (daily) | HTML report accessible, PSI per feature stored |
| 7 | Grafana monitoring dashboards (5 panels) | Score dist, PSI heatmap, performance, model timeline, canary comparison |
| 8 | Alerting on drift thresholds | PSI > 0.25 → email/webhook notification |
| 9 | Score + feature logging | `score_log` and `feature_log` tables populated during scoring |
| 10 | Model staleness alert | Alert fires when champion age > 90 days |

---

## 4.6 Demo Checkpoint (D4)

### What to Show

1. **Train new model** → appears as STAGING in MLflow + model_registry
2. **Promote to SHADOW** → model scores silently, no client impact
3. **Enable canary (20%)** → show Grafana: dual scoring active, challenger scores logged
4. **Canary comparison dashboard** → side-by-side metrics, statistical test result
5. **Auto-promote** → challenger wins → auto-promoted → scoring service hot-reloads
6. **Drift detection** → inject drifted data → Evidently report shows PSI spike → alert fires
7. **Rollback** → rollback to previous champion → scoring restored in < 30s

### Benchmark Targets

| Metric | Target |
|--------|--------|
| Canary overhead (dual scoring) | < 5ms additional P50 latency |
| Model promotion (hot-reload) | < 30 seconds from API call to serving new model |
| Rollback | < 30 seconds |
| Drift report generation | < 5 minutes (100K scored transactions) |
| Score logging overhead | < 1ms (async, non-blocking) |

---

## 4.7 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Canary dual-scoring doubles inference cost | CPU usage increases on scoring nodes | Only score challenger on X% (default 10-20%). LightGBM inference is ~1ms — doubling is negligible. |
| Score log table grows very fast | PostgreSQL storage + query performance degrades | Monthly partitioning + 6-month retention. Sample feature logging (1 in 10). |
| Evidently report generation is slow | Blocks monitoring pipeline | Run on separate worker. Limit to 100K samples per report. |
| Auto-promote promotes a bad model | Production regression | Require minimum canary period (7 days). Require minimum sample size (10K scores). Require no regression on secondary metrics. |
| Drift alerts are noisy (false positives) | Team ignores alerts | Tune PSI thresholds per feature based on historical variability. Start with warning-only, no auto-retrain. |
