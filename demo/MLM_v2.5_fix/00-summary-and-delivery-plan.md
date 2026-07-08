# v2.5 MLM Fixation — Summary & Delivery Plan

## Executive Summary

The v2.5 MLM Fixation is a **Windows-native, backward-compatible** upgrade to the existing `cafs-machinelearning` product. It addresses four areas:

1. **Tier 1**: Eliminate crash cascades and reduce latency from >500ms to ~200ms
2. **Tier 2**: Achieve ~5-10ms scoring latency, support >10M row training, add XGBoost, and deliver backtesting
3. **Tier 3**: Model monitoring dashboard, permutation importance, and prediction explainability (SHAP + PDP)

---

## Delivery Plan

```
Tier 1 — Critical Fixes:
  ├── #1  Remove sys.exit()
  ├── #2  Pre-load model cache at startup
  ├── #3  Split into two Windows Services: scoring + training
  ├── #4  Waitress thread pool (replace single-thread Gevent)
  └── #5  ThreadPoolExecutor for predict_proba
       → Deliverable: ~200ms latency, no crash cascade

Tier 2 — Performance + New Capabilities:
  Scoring path (choose ONE):
  ├── Path A: FastAPI + Uvicorn
  └── Path B: ONNX Runtime in C#    ← RECOMMENDED

  Additional:
  ├── #8   Model hot-reload via FileSystemWatcher
  ├── #9   DataHub paginated extraction
  ├── #10  Isotonic calibration in C#
  ├── #11  Large dataset training (>10M rows via chunked Parquet)
  ├── #12  XGBoost algorithm support
  ├── #13  Additional GBM hyperparameters
  └── #14  Backtesting (validation without retraining)
       → Deliverable: ~5-10ms scoring, 50M+ row training, XGBoost, backtesting

Tier 3 — Monitoring & Explainability:
  ├── Components 1-3: ModelScoreLog + ModelMonitoringSnapshot tables
  ├── Component 4:    MonitoringService (Hangfire batch job)
  ├── Component 5:    REST API controller
  ├── Component 6:    Angular monitoring page
  ├── Component 7:    PSI threshold alerts
  ├── Component 8:    Permutation importance (training artifact + monitoring signal)
  └── Component 9:    Explainability (PDP global + SHAP local — TreeExplainer / DeepExplainer / KernelExplainer)
       → Deliverable: Monitoring dashboard, feature importance, prediction explanations

Testing & UAT: Regression, client validation, deployment
```

---

## Expected Performance — All Tiers

| Metric | Current (v2.4) | After Tier 1 | After Tier 2 (ONNX) | After Tier 3 |
|--------|---------------|-------------|---------------------|-------------|
| P50 latency | > 500ms | ~150-200ms | **~5-10ms** | ~6-12ms (+1ms logging) |
| Throughput | < 50 RPS | ~150-200 RPS | ~500+ RPS | ~450+ RPS |
| Training impact | Service down | Zero | Zero | Zero |
| Crash cascade | Yes | No | No | No |
| Model staleness | 10 days | 10 days (reduced) | Minutes (file watcher) | Minutes |
| Max training rows | ~2-3M | ~2-3M | **50M+** (chunked) | 50M+ |
| Algorithms | LightGBM, RF, GBM | Same | + **XGBoost** | Same |
| Monitoring | None | None | None | ✅ Score dist, PSI, latency |
| Explainability | None | None | None | ✅ PDP + SHAP + Permutation |
| Backtesting | None | None | ✅ (new workflow) | Same |

---

## Team & Constraints

| Role | Person | Primary Responsibility |
|------|--------|----------------------|
| DS / MLE Lead (you) | 1 | Architecture, ML logic (ONNX export, SHAP, training pipeline, backtesting), code review, specs |
| Full-Stack Engineer A | 1 | C# services, Angular UI, SQL, Windows Service packaging |
| Full-Stack Engineer B | 1 | Python service, API endpoints, DB migrations, integration testing |

**Constraints**:
- Engineers have **no prior ML exposure** — ramp-up time required per tier
- Only the lead can implement core ML logic (ONNX conversion, SHAP, permutation importance, chunked training)
- Sequential tier delivery with **testing gates** — no tier starts until previous is validated
- 6-month window: **June 2026 → December 2026** (~26 working weeks)

---

## Revised Delivery Plan (6-Month Calendar)

```
MONTH 1 — June 2026: Tier 1 + Ramp-Up
├── Week 1-2: Engineers onboard codebase
│     • Walk through PythonService.cs, Flask app, model cache, MSI build
│     • Lead prepares dev environment, sets up branch strategy
│     • Engineers implement #1 (sys.exit removal) + #2 (model preload) — low risk, good warmup
│
├── Week 3-4: Core Tier 1
│     • Engineer A: #3 service split (C# Windows Service config, dual-process)
│     • Engineer B: #4 Waitress + #5 ThreadPoolExecutor (Python service changes)
│     • Lead: Reviews, validates no regression
│
└── Week 4 end: ✅ TESTING GATE — Tier 1 validation
      Criteria: <200ms P50, no crash cascade, clean restart, load test passes

MONTH 2 — July 2026: Tier 2a (Scoring Path)
├── Week 5-6: ONNX Foundation
│     • Lead: Builds ONNX export pipeline (skl2onnx, onnxmltools) + validation script
│     • Engineer A: OnnxScoringService.cs + FileSystemWatcher (#7, #8)
│     • Engineer B: Isotonic calibration C# port (#10) + unit tests
│
├── Week 7-8: Integration + DataHub
│     • Engineer A: Hybrid routing in ScoringService.cs (ONNX vs Python fallback)
│     • Engineer B: DataHub paginated extraction (#9)
│     • Lead: End-to-end scoring path validation, latency benchmarking
│
└── Week 8 end: ✅ TESTING GATE — Scoring path validation
      Criteria: <10ms P50 (ONNX path), model hot-reload works, DataHub >100k rows OK

MONTH 3 — August 2026: Tier 2b (New Capabilities)
├── Week 9-10: Large Dataset + XGBoost
│     • Lead: Chunked Parquet ingestion (#11) + XGBoost integration (#12) + ONNX export for XGB
│     • Engineer A: Angular UI — algorithm selector dropdown, new hyperparameter fields (#13)
│     • Engineer B: Backend API for new params + DB schema for hyperparams
│
├── Week 11-12: Backtesting
│     • Lead: Backtesting engine — scoring existing model on new data (#14)
│     • Engineer A: Angular Backtesting page (criteria selection, CSV upload, results table)
│     • Engineer B: BacktestRun DB table, REST controller, integration with Read Replica
│
└── Week 12 end: ✅ TESTING GATE — Tier 2 full validation
      Criteria: 10M+ row training completes, XGBoost trains + scores via ONNX,
      backtesting produces correct metrics, all Tier 1 regressions still pass

MONTH 4 — September 2026: Tier 3a (Monitoring Infrastructure)
├── Week 13-14: Score Logging + DB
│     • Engineer B: ModelScoreLog table + async insert in scoring path (Component 1-2)
│     • Engineer A: ModelMonitoringSnapshot table + MonitoringService skeleton (Component 3-4)
│     • Lead: Teaches PSI concept to engineers, reviews schema
│
├── Week 15-16: Monitoring API + UI
│     • Engineer A: Angular monitoring page — PrimeNG charts, model selector (Component 6)
│     • Engineer B: REST API endpoints + Hangfire scheduled job (Component 4-5)
│     • Lead: PSI calculation logic + threshold alerting (Component 7)
│
└── Week 16 end: ✅ TESTING GATE — Monitoring validation
      Criteria: Score logging <1ms overhead, PSI computes correctly,
      dashboard renders with real data, alerts fire on synthetic drift

MONTH 5 — October 2026: Tier 3b (Explainability)
├── Week 17-18: Permutation Importance + PDP
│     • Lead: Permutation importance at training time + PDP computation (Component 8)
│     • Lead: SHAP TreeExplainer integration + batch job (Component 9)
│     • Engineer A: Angular explainability page — feature importance bar charts, PDP plots
│     • Engineer B: ExplainabilityLog table + REST endpoints + async job infrastructure
│
├── Week 19-20: SHAP On-Demand + KernelSHAP/DeepSHAP Fallback
│     • Lead: On-demand SHAP endpoint (async with polling) + DeepExplainer for NNs + KernelExplainer for custom models
│     • Engineer A: Angular — single-prediction explanation view (waterfall chart)
│     • Engineer B: Integration testing, API docs, error handling
│
└── Week 20 end: ✅ TESTING GATE — Explainability validation
      Criteria: Permutation importance stored at training, SHAP returns in <5s,
      PDP renders correctly, DeepSHAP/KernelSHAP works for non-tree models

MONTH 6 — November–December 2026: Hardening + UAT
├── Week 21-22: Integration Testing
│     • Full regression suite across all tiers
│     • Load testing (500+ RPS sustained for 1hr)
│     • Edge cases: empty models, corrupt ONNX files, missing features, DB timeout
│
├── Week 23-24: UAT with Stakeholders
│     • Demo to Product — walk through each capability
│     • Fix any feedback items
│     • MSI packaging + installer validation on clean Windows Server
│
├── Week 25-26: Release Prep
│     • Documentation finalization
│     • Release notes
│     • Deployment runbook + rollback procedure
│     • Buffer for unexpected issues
│
└── End of December: 🚀 v2.5 RELEASE
```

---

## Effort Summary (Revised)

| Phase | Calendar | Work Ownership | Notes |
|-------|----------|---------------|-------|
| Tier 1 + onboarding | Month 1 (4 weeks) | All 3 | Includes 1-2 weeks for engineers to learn the codebase |
| Tier 2a — Scoring path | Month 2 (4 weeks) | Lead: ONNX export; Eng A: C# scoring; Eng B: calibration/DataHub | Lead teaches ONNX concept in Week 5 |
| Tier 2b — Capabilities | Month 3 (4 weeks) | Lead: ML logic (chunked, XGB, backtest); Engineers: UI + API | Engineers now comfortable with the codebase |
| Tier 3a — Monitoring | Month 4 (4 weeks) | Lead: PSI logic; Engineers: DB + API + Angular | Lead teaches PSI/drift concepts |
| Tier 3b — Explainability | Month 5 (4 weeks) | Lead: SHAP/PDP; Engineers: UI + API | Most ML-heavy phase for lead |
| Hardening + UAT | Month 6 (4 weeks) | All 3 | Testing, packaging, stakeholder feedback |

**Why this works in 6 months**:
- Each tier gets a full month — enough for learning + implementation + testing gate
- Lead handles all ML-specific code (no blocked dependency on engineers learning ML)
- Engineers focus on what they know: C#, Angular, SQL, REST APIs, Windows Services
- Testing gates prevent compounding bugs across tiers
- Month 6 buffer absorbs surprises without rushing the release

**Key risks to the timeline**:
- If Engineer A or B leaves → single point of failure on C# or Python service work
- If ONNX export for XGBoost has compatibility issues → may need extra week in Month 3
- If Angular 7 PrimeNG charting is too limited → may need Chart.js direct integration (adds ~3 days)

---

## DB Schema Additions

```sql
-- New table: ModelScoreLog (Tier 3 — monitoring)
CREATE TABLE ModelScoreLog (
    Id BIGINT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    Score DECIMAL(10,6) NOT NULL,
    RiskBand NVARCHAR(20),
    LatencyMs INT,
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ModelScoreLog_ModelId_CreatedAt (ModelId, CreatedAt)
);

-- New table: ModelMonitoringSnapshot (Tier 3 — monitoring)
CREATE TABLE ModelMonitoringSnapshot (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    SnapshotDate DATE NOT NULL,
    ScoringVolume INT,
    PSI DECIMAL(10,6),
    P50LatencyMs INT,
    P95LatencyMs INT,
    P99LatencyMs INT,
    ScoreDistribution NVARCHAR(MAX),
    RiskBandDistribution NVARCHAR(MAX),
    FeatureImportanceCorrelation DECIMAL(5,4),
    TopFeaturesDrifted NVARCHAR(MAX),
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ModelMonitoringSnapshot_ModelId (ModelId, SnapshotDate)
);

-- New table: BacktestRun (Tier 2 — backtesting)
CREATE TABLE BacktestRun (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    DataSource NVARCHAR(20) NOT NULL,
    CriteriaConfig NVARCHAR(MAX),
    FileName NVARCHAR(255),
    RecordCount INT,
    PositiveRate DECIMAL(10,6),
    AucRoc DECIMAL(10,6),
    AucPr DECIMAL(10,6),
    KsStatistic DECIMAL(10,6),
    PsiVsTraining DECIMAL(10,6),
    ResultsJson NVARCHAR(MAX),
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    CreatedBy NVARCHAR(100),
    INDEX IX_BacktestRun_ModelId (ModelId, CreatedAt)
);

-- New table: ExplainabilityLog (Tier 3 — explainability)
CREATE TABLE ExplainabilityLog (
    Id BIGINT IDENTITY(1,1) PRIMARY KEY,
    ModelId INT NOT NULL,
    TransactionId NVARCHAR(100),
    Score DECIMAL(10,6),
    ExplanationMethod NVARCHAR(20),
    ExplanationJson NVARCHAR(MAX),
    CreatedAt DATETIME2 DEFAULT GETUTCDATE(),
    INDEX IX_ExplainabilityLog_ModelId (ModelId, CreatedAt)
);

-- ALTER existing table: TrainingModelRun (Tier 3 — explainability artifacts)
ALTER TABLE TrainingModelRun
    ADD PermutationImportance NVARCHAR(MAX),
        PdpResults NVARCHAR(MAX),
        GlobalExplainability NVARCHAR(MAX);
```

---

## Key Dependencies

| Dependency | Required By | Notes |
|-----------|------------|-------|
| `waitress` | Tier 1 (#4) | Windows-compatible WSGI server, pip install |
| `Microsoft.ML.OnnxRuntime` | Tier 2 (#7) | NuGet package, Windows-first |
| `skl2onnx` / `onnxmltools` | Tier 2 (#7, #12) | ONNX export for LightGBM + XGBoost |
| `pyarrow` | Tier 2 (#11) | Parquet read/write for large dataset training |
| `xgboost` | Tier 2 (#12) | New algorithm, pip install (Windows wheels) |
| `Hangfire` | Tier 3 | NuGet — batch jobs for monitoring + explainability |
| `PrimeNG (p-chart)` | Tier 3 (#6) | Angular 7 compatible charting (Chart.js wrapper) |
| `shap` | Tier 3 (#9) | SHAP TreeExplainer / DeepExplainer / KernelExplainer for local explanations |
