# Product Requirements Document  
## GBG Fraud Realtime ML Platform (FraudML Platform)  
**Version:** 1.0  
**Date:** April 24, 2026  
**Status:** Draft — For Internal Review  
**Author:** GBG Analytics Team  
**Classification:** Private / Confidential  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Background & Strategic Context](#2-background--strategic-context)
3. [Product Vision & Goals](#3-product-vision--goals)
4. [Target Users & Personas](#4-target-users--personas)
5. [Product Architecture Overview](#5-product-architecture-overview)
6. [Functional Requirements](#6-functional-requirements)
   - 6.1 [Feature Platform](#61-feature-platform)
   - 6.2 [ML Training Hub](#62-ml-training-hub)
   - 6.3 [Model Serving & API Gateway](#63-model-serving--api-gateway)
   - 6.4 [Instinct Integration Layer](#64-instinct-integration-layer)
   - 6.5 [Operator Dashboard (UI)](#65-operator-dashboard-ui)
   - 6.6 [Job Orchestration & Scheduling](#66-job-orchestration--scheduling)
   - 6.7 [Model Monitoring & Observability](#67-model-monitoring--observability)
   - 6.8 [Multi-Tenancy & Access Control](#68-multi-tenancy--access-control)
   - 6.9 [Installation & Deployment](#69-installation--deployment)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Release Milestones](#8-release-milestones)
9. [Technical Debt & Migration Plan from Legacy](#9-technical-debt--migration-plan-from-legacy)
10. [Open Questions & Decisions](#10-open-questions--decisions)

---

## 1. Executive Summary

GBG's existing machine learning product (`cafs-machinelearning`, referred to as **Legacy ML**) has not been updated in approximately five years. It delivers core capabilities — model training, batch scoring, and Windows-based deployment — but carries critical gaps: a Python 3.8 EOL stack, a ZeroMQ IPC concurrency ceiling of 10 simultaneous requests, no real-time feature enrichment, no model monitoring, no calibration, and a Windows-only deployment model that is incompatible with modern cloud-native hosting.

A challenger prototype (`fraud-realtime-ml-prototype`) has been built that solves the most critical technical limitations: full async serving, Redis-backed sliding-window features, sub-5ms feature retrieval via a pipelined Redis reader, probability calibration, training-serving consistency via Feast, and a config-driven training pipeline with MLflow experiment tracking.

This PRD formalises the path from prototype to **GBG Fraud Realtime ML Platform** — a complete, production-ready, private ML platform for banking fraud detection that:

- Preserves and extends all capabilities from the legacy product
- Adds the full set of modern ML platform capabilities (monitoring, champion-challenger, SHAP, anomaly detection, hyperparameter search, auto-retraining, canary deployment)
- Integrates bidirectionally with the existing **GBG Instinct** fraud detection system
- Supports multi-tenant deployment for banking clients
- Ships as a deployable product via a Docker-based cloud installer **or** a Windows on-premise package — both fully private, no SaaS dependency

---

## 2. Background & Strategic Context

### 2.1 Legacy ML Platform Summary (`cafs-machinelearning v2.3.2`)

| Layer | Stack | Status |
|---|---|---|
| Host API | ASP.NET Core 6.0 + Windows Service | Functional but Windows-locked |
| ML Engine | Python 3.8, Flask/gevent | EOL Python, max 10 concurrent ZeroMQ slots |
| ML Algorithms | Neural Network (TF 2.3/Keras 2.4), LightGBM 3.0, Random Forest, Isolation Forest, Custom plugins | TF/Keras stack is broken against modern pip; LightGBM 3.0 is two major versions behind |
| Job Scheduler | Hangfire 1.8 | Functional — retained in platform design |
| Database | SQL Server / MySQL / PostgreSQL (multi-DB routing) | Good — must be preserved |
| Frontend | TypeScript 3.5 ASP.NET MVC | Functional, dated |
| Deployment | WiX MSI installer, Windows Service | Windows-only |
| Security | Committed secrets, EOL Data Protection keys | Must be remediated |

**Root cause of decline:** No breaking changes shipped since ~2021. The Python ML ecosystem moves fast; a five-year freeze means every ML dependency has breaking API changes that require code rewrites, not just version bumps.

### 2.2 Challenger Prototype Summary (`fraud-realtime-ml-prototype`)

| Layer | Stack | Status |
|---|---|---|
| Serving API | FastAPI 0.111 + uvicorn + asyncio | Production-ready architecture |
| ML Algorithms | XGBoost 2.0, LightGBM 4.3, RandomForest | Modern, calibrated, config-driven |
| Feature Store | Feast 0.40 + Redis 7 (online) + DuckDB + dbt (offline) | Solid design; DuckDB not production-safe as sole offline store |
| Training Pipeline | Config-driven YAML experiments, MLflow 2.13, temporal OOT splits, calibration | Production-ready training pipeline |
| Serving Latency | ~2ms feature retrieval (Feast SDK bypassed), parallel async fetches | Exceeds legacy by ~300x throughput ceiling |
| Deployment | Docker Compose (Postgres, Redis, API) | Cloud-native; no Windows installer yet |
| UI | None | Missing — largest gap |
| Multi-tenancy | None | Missing |
| Monitoring | `model_score_log` table only | Partial |
| Instinct Integration | None | Missing |

### 2.3 Why This Platform, Why Now

1. **EU AI Act (2026)** — Article 9 and 13 require explainability, audit trails, and model monitoring for AI systems used in financial decisions. The legacy platform has none of these.
2. **Banking client expansion** — New clients in ID, MY, PH, TH, AU are actively requesting ML capabilities. The legacy platform's Windows-only constraint is a blocker for cloud-hosted clients.
3. **Model performance degradation** — Without monitoring or auto-retraining, deployed models at banking clients have not been updated to reflect post-COVID fraud pattern shifts.
4. **Competitive positioning** — Competing ML platforms (H2O.ai, DataRobot, AWS SageMaker) offer end-to-end MLOps. GBG's differentiation is deep fraud domain knowledge + real-time transaction enrichment + Instinct integration.

---

## 3. Product Vision & Goals

### Vision Statement

> A self-contained, private ML platform that any GBG banking client can deploy — on-premise or in the cloud — to build, serve, monitor, and iterate on fraud detection models, fully integrated with the Instinct decisioning engine, requiring no external SaaS dependencies and no MLOps expertise from the bank's operations team.

### Product Goals

| Goal | Success Metric |
|---|---|
| **G1** Replace the legacy ML scoring path | P95 scoring latency < 50ms at 500 TPS; zero ZeroMQ IPC in critical path |
| **G2** Enable non-technical operators to manage models | An operations analyst can promote a new model without writing code or using a terminal |
| **G3** Full EU AI Act compliance | Every scored transaction has a logged feature vector, model version, and SHAP explanation |
| **G4** Instinct bidirectional integration | Instinct rules can consume ML scores; ML training can use Instinct alert labels as ground truth |
| **G5** Multi-tenant isolation | Two banking clients on the same deployment have zero data cross-contamination |
| **G6** Modern algorithm coverage | Neural Network, Gradient Boosting, Isolation Forest, autoencoder, SHAP, hyperparameter search all available |
| **G7** Deployment flexibility | Single command install on cloud (Docker/Helm) or Windows Server 2022 on-premise |

---

## 4. Target Users & Personas

### Persona 1 — Fraud Operations Analyst (Primary, Non-Technical)
**At:** Client bank (e.g. CIMB Niaga, BSN, RHB)  
**Goal:** Know whether the fraud model is working, see flagged transactions, investigate anomalies  
**Touchpoints:** Operator Dashboard — Monitoring page, Score Explorer  
**Does NOT:** Write code, run CLI commands, manage infrastructure  

### Persona 2 — Risk Data Scientist (Primary)
**At:** GBG Analytics team or client bank data science team  
**Goal:** Train new model versions, run experiments, compare performance, promote models  
**Touchpoints:** Training Hub UI, MLflow UI, Model Hub page, feature contract definitions  
**Pain point with legacy:** No calibration, no experiment tracking, no OOT validation, no SHAP  

### Persona 3 — ML Platform Engineer (Secondary)
**At:** GBG product/engineering team  
**Goal:** Manage deployments, run offline pipelines, configure tenants, monitor infrastructure  
**Touchpoints:** Pipeline Control UI, Docker/Helm configs, Makefile, Airflow DAGs  

### Persona 4 — Fraud Product Manager (Secondary)
**At:** Client bank  
**Goal:** Understand model performance trends over time, prepare for regulatory reviews  
**Touchpoints:** Monitoring dashboard, model audit export  

### Persona 5 — Instinct System Administrator (Secondary)
**At:** GBG / client bank  
**Goal:** Configure which ML model scores flow into which Instinct alert rules, enable SSO  
**Touchpoints:** Instinct Integration configuration panel, SSO settings  

---

## 5. Product Architecture Overview

### 5.1 High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          GBG FraudML Platform                               │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Operator Dashboard (Streamlit)                   │    │
│  │  Monitoring │ Model Hub │ Score Explorer │ Pipeline │ Training Hub  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                        │
│  ┌──────────────┐   ┌──────────────▼─────────────────┐                     │
│  │  Instinct    │   │         FastAPI ML Gateway       │                    │
│  │  Integration │◄──│  /score  /shadow  /health        │                    │
│  │  Layer       │   │  /tenants/{id}/score             │                    │
│  └──────────────┘   └──────────────┬─────────────────-┘                    │
│                                    │                                        │
│          ┌─────────────────────────┼────────────────────────┐              │
│          │                         │                        │              │
│  ┌───────▼──────┐   ┌──────────────▼──────────┐  ┌────────▼────────┐     │
│  │  Feature     │   │     Model Registry       │  │  Job            │     │
│  │  Platform    │   │   MLflow + Promote API   │  │  Orchestrator   │     │
│  │              │   │   Champion/Challenger    │  │  (Prefect/      │     │
│  │  Feast       │   │   Canary routing         │  │   Airflow)      │     │
│  │  Redis       │   │   SHAP explainer         │  │                 │     │
│  │  DuckDB/PG   │   └──────────────────────────┘  └─────────────────┘     │
│  │  dbt         │                                                          │
│  └──────────────┘                                                          │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │           Multi-Tenant Data Layer                                     │  │
│  │   Postgres (schema-per-tenant)  │  Redis (namespace-per-tenant)      │  │
│  │   DuckDB/S3 (path-per-tenant)   │  Feast registry (tag-per-tenant)   │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │           Monitoring & Observability                                  │  │
│  │   Model drift (PSI/KS)  │  Feature freshness  │  Grafana/Prometheus  │  │
│  │   Score distribution    │  SHAP log            │  Alert engine       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                │                                    │
   ┌────────────▼──────────┐         ┌───────────────▼──────────────┐
   │   GBG Instinct         │         │   Client Bank Data Sources    │
   │   (fraud rules engine) │         │   (transactions, users,       │
   │   SSO cookie shared    │         │    labels, login events)      │
   └────────────────────────┘         └──────────────────────────────┘
```

### 5.2 Key Architectural Principles

1. **Separate concerns between scoring and training** — The scoring API and the training pipeline run in separate containers. A long training job never degrades API latency.
2. **No SaaS dependencies at runtime** — All components (Feast, Redis, Postgres, MLflow, Prefect) run inside the client deployment. No phone-home required.
3. **Tenant-first data isolation** — Every Postgres query, Redis key, and Feast feature view is namespaced by `tenant_id`. The platform starts multi-tenant from day one.
4. **Graceful degradation at serving time** — Redis miss → default features (0). Feast miss → default features. Model not loaded → 503. No cascading failure.
5. **Instinct-first integration** — The platform is designed as a scoring enrichment layer for Instinct, not a replacement. Instinct rules remain the decision authority; ML provides the score input.

---

## 6. Functional Requirements

### 6.1 Feature Platform

#### 6.1.1 Offline Feature Pipeline

| ID | Requirement | Priority |
|---|---|---|
| FP-01 | dbt pipeline produces `fct_user_features`, `fct_device_features`, `fct_merchant_features`, `fct_training_dataset` from raw Postgres tables | P0 |
| FP-02 | Offline store supports both DuckDB (on-premise) and PostgreSQL + Parquet on local disk or S3-compatible object storage (cloud) | P0 |
| FP-03 | Feast applies feature definitions from a shared registry (Postgres-backed in cloud mode, SQLite in single-node on-premise) | P0 |
| FP-04 | Feast `feast materialize` job populates Redis online store from Parquet files | P0 |
| FP-05 | Feature versioning follows `<entity>_batch_fv_v<N>` naming; breaking logic changes increment N; active `FeatureService` pins to specific versions | P0 |
| FP-06 | All feature definitions have a canonical `feature_contract.yaml` — source, dtype, description, entity, update frequency | P1 |
| FP-07 | Feature freshness monitoring: alert if any Feast-materialized feature view has not been refreshed within 2× its expected cadence | P1 |
| FP-08 | Support per-tenant feature namespacing in Redis (`{tenant_id}:fraud:user:{user_id}:txn_ts`) and per-tenant Parquet path (`{tenant_id}/fct_user_features_v1.parquet`) | P0 |

#### 6.1.2 Online Feature Pipeline

| ID | Requirement | Priority |
|---|---|---|
| FP-09 | Redis sorted sets maintain sliding-window counters per user and device: 5m, 10m, 1h windows for txn count, txn amount, distinct merchants, and failed logins | P0 |
| FP-10 | Online feature writes are triggered by transaction events from an event stream (Kafka topic in cloud mode; direct call in on-premise single-node mode) | P1 (P0 for cloud) |
| FP-11 | All 11 online feature Redis reads are batched into a single pipeline round trip per scoring request | P0 |
| FP-12 | Per-entity TTL caches (user 60s, device 60s, merchant 60s) reduce Redis reads for Feast offline features | P0 |
| FP-13 | Feast SDK bypassed at serving time; direct Redis binary read with pre-computed mmh3 field hashes | P0 |
| FP-14 | Cold-start handling: entities with no Redis history receive population-average defaults derived from DuckDB aggregates, not zeros | P1 |

#### 6.1.3 Feature Quality & Lineage

| ID | Requirement | Priority |
|---|---|---|
| FP-15 | Every inference logs the full online feature vector served to `online_feature_log` table (Postgres) for training-serving consistency validation | P0 |
| FP-16 | Automated null rate and out-of-range checks run after every `feast materialize` step; failed checks block pipeline and raise alert | P1 |
| FP-17 | Feature lineage: dbt `docs generate` artifacts are surfaced in the Operator Dashboard (link to dbt lineage graph) | P2 |

---

### 6.2 ML Training Hub

#### 6.2.1 Supported Algorithms

| Algorithm | Framework | Use Case |
|---|---|---|
| **Gradient Boosting (LightGBM)** | LightGBM 4.x | Primary supervised fraud classifier |
| **Gradient Boosting (XGBoost)** | XGBoost 2.x | Alternative supervised fraud classifier |
| **Random Forest** | scikit-learn 1.4+ | Baseline / interpretability |
| **Neural Network (Tabular)** | PyTorch 2.x (replace Keras/TF) | Complex non-linear patterns |
| **Isolation Forest** | scikit-learn + isotree | Unsupervised anomaly detection |
| **Autoencoder** | PyTorch 2.x | Unsupervised anomaly (reconstruction error) |
| **Custom Plugin** | Any Python class implementing `BaseModel` interface | Client-specific algorithms (preserves legacy plugin capability) |

> **Rationale for PyTorch over TF/Keras:** TF 2.3/Keras 2.4 in the legacy platform has broken dependencies that cannot be resolved without major rewrites. PyTorch 2.x has a stable API, first-class scikit-learn-compatible wrappers (`skorch`), and is the dominant research framework. The `skorch` wrapper integrates seamlessly with the existing `ColumnTransformer` + `CalibratedClassifierCV` pipeline.

| ID | Requirement | Priority |
|---|---|---|
| TR-01 | All algorithms are selectable via `model.type` in `training_config.yaml` — no code changes required | P0 |
| TR-02 | Training pipeline supports `ColumnTransformer` preprocessing: StandardScaler, MinMaxScaler, RobustScaler, OneHotEncoder, OrdinalEncoder, passthrough | P0 |
| TR-03 | Probability calibration: none / sigmoid / isotonic / beta — selectable via config | P0 |
| TR-04 | Train/validation split: temporal (OOT) or random stratified — configurable | P0 |
| TR-05 | Training dataset includes both Feast offline features (via PIT join) and actual served online features (from `online_feature_log`) to eliminate training-serving skew | P0 |
| TR-06 | Hyperparameter search via **Optuna** — `search` block in config defines parameter space; results stored in MLflow as child runs | P1 |
| TR-07 | All experiments tracked in MLflow: params, metrics (ROC-AUC, PR-AUC, Brier, ECE), model artifacts, feature importance charts, training config | P0 |
| TR-08 | Delayed label ingestion: a `label_ingest` pipeline accepts chargebacks/confirmed fraud labels from external systems (Instinct alert closure, SWIFT MX.001) and back-fills `fraud_labels` table | P1 |
| TR-09 | Auto-retraining trigger: PSI > 0.2 on any tier-1 feature OR monthly scheduled run kicks off training pipeline via Prefect/Airflow | P1 |
| TR-10 | Training run produces: base model `.pkl`, preprocessor `.pkl`, calibrated model `.pkl`, `model_meta.json`, `feature_importances.csv`, SHAP summary plot | P0 |

#### 6.2.2 Model Evaluation

| ID | Requirement | Priority |
|---|---|---|
| TR-11 | Evaluation report: ROC-AUC, PR-AUC, recall@precision≥0.5, confusion matrix, Brier score, Expected Calibration Error, reliability diagram | P0 |
| TR-12 | SHAP TreeExplainer (XGBoost/LightGBM/RF) or KernelExplainer (fallback): per-prediction top-5 feature contributions stored in `shap_log` table | P0 (store) / P1 (real-time) |
| TR-13 | Population-level SHAP summary exported as artifact per training run | P0 |
| TR-14 | Feature importance comparison between any two MLflow runs via UI | P1 |
| TR-15 | Threshold selection: fixed OR recall-target (search precision-recall curve) — result stored in `model_meta.json` | P0 |

#### 6.2.3 Champion-Challenger Framework

| ID | Requirement | Priority |
|---|---|---|
| TR-16 | **Shadow scoring**: any model version can be designated as "shadow" — it scores every request in a background task, result logged to `shadow_score_log` but NOT returned to client | P1 |
| TR-17 | **Traffic split (A/B)**: platform supports routing X% of live scoring requests to a challenger model (remaining to champion); split % configurable per tenant via dashboard | P2 |
| TR-18 | **Champion promotion gate**: UI shows side-by-side champion vs challenger metrics on live traffic (ROC-AUC, flagging rate, false positive rate); one-click promotion after review | P1 |
| TR-19 | **Canary deployment**: new model version receives 5% of traffic; automated rollback if error rate exceeds threshold | P2 |
| TR-20 | **Rollback**: any previous model version can be restored from MLflow artifact registry via one click in the Model Hub UI | P1 |

---

### 6.3 Model Serving & API Gateway

#### 6.3.1 Scoring API

| ID | Requirement | Priority |
|---|---|---|
| SV-01 | `POST /api/v1/tenants/{tenant_id}/score` — primary scoring endpoint, returns fraud score, risk band, model version, SHAP top-5 features (optional flag), feature source health | P0 |
| SV-02 | `POST /api/v1/tenants/{tenant_id}/score/batch` — batch scoring endpoint accepting up to 1,000 transactions per request; async processing, returns `job_id` | P1 |
| SV-03 | `GET /api/v1/tenants/{tenant_id}/score/batch/{job_id}` — batch result retrieval | P1 |
| SV-04 | `GET /api/v1/health` — platform-level health (all services) | P0 |
| SV-05 | `GET /api/v1/tenants/{tenant_id}/health` — tenant-specific health (model loaded, Redis namespace populated, Feast features fresh) | P0 |
| SV-06 | P95 latency ≤ 50ms at 500 TPS (single node: 4 CPU, 8GB RAM) | P0 |
| SV-07 | Graceful degradation: Redis unavailable → score with offline features only + `redis_online_ok: false` flag; Feast unavailable → score with online + request features only + `feast_offline_ok: false` flag | P0 |
| SV-08 | JWT authentication for API access; per-tenant API keys managed via admin panel | P0 |
| SV-09 | Rate limiting per API key (configurable per tenant) | P1 |
| SV-10 | Request/response schema versioned via `Accept: application/vnd.gbg.fraudml.v1+json` header | P2 |

#### 6.3.2 Instinct Score Webhook

| ID | Requirement | Priority |
|---|---|---|
| SV-11 | Platform exposes a Webhook endpoint that Instinct can call at alert evaluation time: `POST /api/v1/tenants/{tenant_id}/instinct/score` — receives Instinct alert payload, returns ML score | P0 |
| SV-12 | Score is returned within Instinct's configurable timeout (default 30s); if platform times out, Instinct continues without ML score (non-blocking) | P0 |
| SV-13 | Shared cookie SSO with Instinct — users authenticated to Instinct are authenticated to the ML platform dashboard without re-login | P1 |

---

### 6.4 Instinct Integration Layer

This is the most strategically critical capability for existing GBG banking clients. The ML platform is not a standalone product — it is an enrichment and extension layer for the Instinct decision engine.

#### 6.4.1 Score Consumption by Instinct Rules

| ID | Requirement | Priority |
|---|---|---|
| IN-01 | Instinct alert rules can reference `ml_fraud_score` as a condition variable (e.g., `ml_fraud_score > 0.8 AND amount > 5000`) | P0 |
| IN-02 | Instinct alert rules can reference `ml_risk_band` as a string variable (`ml_risk_band = 'critical'`) | P0 |
| IN-03 | Instinct alert rules can reference the top SHAP feature for a transaction (`ml_top_feature_1 = 'user_txn_count_5m'`) | P2 |
| IN-04 | ML score is available as a custom field on the Instinct alert record — visible in the Instinct case management UI | P1 |
| IN-05 | ML score webhook must be configured per tenant in the Instinct system configuration (`MachineLearning.Url` / `AppSettings`), pointing to `POST /api/v1/tenants/{tenant_id}/instinct/score` | P0 |

#### 6.4.2 Label Feedback from Instinct to ML Platform

| ID | Requirement | Priority |
|---|---|---|
| IN-06 | When an Instinct alert is closed as Confirmed Fraud by an analyst, the corresponding `transaction_id` is written to the ML platform's `fraud_labels` table via the Instinct-to-ML feedback API | P1 |
| IN-07 | When an Instinct alert is closed as False Positive, the corresponding label is written as `is_fraud=0` to `fraud_labels` | P1 |
| IN-08 | `POST /api/v1/tenants/{tenant_id}/labels` — label ingestion endpoint for Instinct feedback and external chargeback feeds | P1 |
| IN-09 | Feedback loop metrics are visible on the Monitoring dashboard: label ingestion lag (days between transaction and label), label coverage % (labelled / total scored) | P1 |

#### 6.4.3 SSO & Session Sharing

| ID | Requirement | Priority |
|---|---|---|
| IN-10 | Shared authentication cookie (`.AspNet.SharedCookie` pattern from legacy platform) between Instinct and ML platform dashboard | P1 |
| IN-11 | Platform supports JWT token issued by Instinct as Bearer token for API access | P1 |
| IN-12 | Platform RBAC roles map to Instinct roles: `ML_VIEWER` (read-only dashboard), `ML_ANALYST` (model training), `ML_ADMIN` (promote, configure) | P1 |

#### 6.4.4 Legacy Migration Path

For existing clients currently running `cafs-machinelearning` alongside Instinct:

| ID | Requirement | Priority |
|---|---|---|
| IN-13 | Migration script exports `TrainingModel`, `TrainingModelRun`, and `FrozenModel` records from the legacy SQL Server schema to MLflow artifacts | P1 |
| IN-14 | Legacy `AppSettings.ZeroMQPort` / `AppSettings.ZeroMQ` configuration is replaced by `AppSettings.MLPlatformUrl` pointing to the new platform's score endpoint | P0 |
| IN-15 | Parallel running mode: Instinct can call both legacy ZeroMQ score and new platform score simultaneously for a transition period; result routing configurable per alert rule | P1 |

---

### 6.5 Operator Dashboard (UI)

Built with **Streamlit** as a separate Docker service on port 8501. Authentication via HTTP basic auth (nginx reverse proxy) for initial release; SSO cookie integration in v1.1.

#### 6.5.1 Page: Live Monitoring

**Audience:** Fraud Operations Analyst

| ID | Requirement |
|---|---|
| UI-01 | System health banner: API status (green/amber/red), Redis connectivity, model version loaded, last Feast materialization timestamp. Auto-refreshes every 30 seconds |
| UI-02 | KPI row (last 24h): total scored, % flagged, % critical, average fraud score, P95 latency |
| UI-03 | Risk band breakdown: donut chart (low / medium / high / critical counts) with 24h / 7d / 30d selector |
| UI-04 | Hourly flagging rate trend: line chart over last 48 hours |
| UI-05 | Feature source health gauges: `feast_offline_ok` rate and `redis_online_ok` rate (last 1h). Amber alert if < 95% |
| UI-06 | Real-time score stream: live table of last 50 scored transactions (auto-refresh), columns: transaction_id, user_id, score, risk_band, model_version, scored_at |
| UI-07 | Tenant selector dropdown (top of page) — all pages are tenant-scoped |

#### 6.5.2 Page: Model Hub

**Audience:** Risk Data Scientist, Platform Engineer

| ID | Requirement |
|---|---|
| UI-08 | Table of all MLflow experiment runs for the current tenant: run_id, model_name, model_type, val_roc_auc, val_pr_auc, threshold, calibration, split_method, training timestamp |
| UI-09 | Currently active champion model marked with a badge; shadow model (if any) marked separately |
| UI-10 | Side-by-side comparison: select any two runs, display grouped bar chart of ROC-AUC, PR-AUC, Brier score, flagging rate |
| UI-11 | Feature importance viewer: horizontal bar chart from MLflow `feature_importances.csv` artifact for any selected run |
| UI-12 | SHAP summary plot displayed from MLflow artifact (beeswarm or bar chart, selectable) |
| UI-13 | Promote to Champion button: opens confirmation dialog showing current champion vs selected challenger metrics diff; on confirm, calls `scripts/promote_model.py` |
| UI-14 | Set as Shadow button: designates selected model as shadow scorer (logs to `shadow_score_log`, not returned in API response) |
| UI-15 | Rollback to previous champion: one-click from run table |
| UI-16 | Reliability diagram (calibration curve) for selected run |

#### 6.5.3 Page: Score Explorer

**Audience:** Fraud Operations Analyst, Fraud Product Manager

| ID | Requirement |
|---|---|
| UI-17 | Filter panel: date range picker, risk band multiselect, user_id / device_id / transaction_id text search, model version filter |
| UI-18 | Paginated results table from `model_score_log` (50 rows/page); sortable by scored_at, fraud_score |
| UI-19 | Row click → Transaction Detail panel: all score fields + joined `online_feature_log` for that `transaction_id` showing the 14 online features served at that moment |
| UI-20 | Fraud score gauge (Plotly indicator) for selected transaction with threshold marker |
| UI-21 | SHAP waterfall chart for selected transaction (top 10 feature contributions) |
| UI-22 | Export to CSV: export current filtered results (max 10,000 rows) |

#### 6.5.4 Page: Pipeline Control

**Audience:** Platform Engineer, ML Ops Analyst

| ID | Requirement |
|---|---|
| UI-23 | Pipeline status strip showing last successful run timestamp for each step: Export PG → DuckDB, dbt run, Feast apply, Feast materialize, Stream events |
| UI-24 | Manual trigger buttons for each pipeline step; each step streams stdout into `st.status` expandable block |
| UI-25 | Stream event toggle: start/stop the live transaction simulator (for testing/development environments) |
| UI-26 | Pipeline run history: last 10 executions with status (success/fail), duration, error message |
| UI-27 | Data volume metrics: raw_transactions row count, DuckDB feature row counts, Redis key counts per namespace |

#### 6.5.5 Page: Training Config & Launch

**Audience:** Risk Data Scientist

| ID | Requirement |
|---|---|
| UI-28 | Config form renders `training_config.yaml` fields as UI controls: model type dropdown, split method radio, preprocessing selects, calibration select, threshold strategy |
| UI-29 | Saved experiments listed as cards (from `training/experiments/*.yaml`); load any to populate the form |
| UI-30 | Save config button writes the selected experiment YAML to disk |
| UI-31 | Launch training button runs `make train` via subprocess; output streams live into `st.status` block |
| UI-32 | Post-training summary: metric cards for new run, with prompt to promote or set as shadow via direct navigation |

#### 6.5.6 Page: Model Monitoring

**Audience:** Risk Data Scientist, Fraud Product Manager

| ID | Requirement |
|---|---|
| UI-33 | Population Stability Index (PSI) chart per feature over time (rolling 30-day window vs training baseline) |
| UI-34 | Kolmogorov-Smirnov drift test results per feature — highlight features with KS statistic > 0.1 |
| UI-35 | Score distribution drift: histogram of fraud scores (last 7d vs training period baseline) |
| UI-36 | Label feedback dashboard: label ingestion lag, coverage %, false positive rate trend |
| UI-37 | Drift alert log: table of triggered drift alerts with feature name, PSI value, timestamp |

#### 6.5.7 Page: Tenant Administration (Admin Only)

**Audience:** ML Platform Engineer, GBG Implementation Team

| ID | Requirement |
|---|---|
| UI-38 | Create/deactivate tenant; assign database schema, Redis namespace prefix, Feast feature service |
| UI-39 | API key management: generate, rotate, revoke per-tenant API keys |
| UI-40 | Instinct integration config: set ML platform webhook URL in Instinct `AppSettings`; test connection |
| UI-41 | User role assignment: assign `ML_VIEWER`, `ML_ANALYST`, `ML_ADMIN` per user per tenant |

---

### 6.6 Job Orchestration & Scheduling

**Orchestrator choice:** **Prefect** (cloud mode) or **APScheduler embedded** (on-premise single-node mode). Prefect is preferred for cloud deployments as it provides a UI, retry logic, run history, and alerting without requiring a full Airflow setup.

| ID | Requirement | Priority |
|---|---|---|
| JO-01 | **Offline pipeline DAG**: Export PG → DuckDB → dbt run → Feast apply → Feast materialize. Configurable schedule per tenant (default: daily at 02:00 UTC). On failure: alert + retry ×3 | P0 |
| JO-02 | **Training trigger DAG**: triggered by PSI alert (PSI > 0.2 on any tier-1 feature) OR manual trigger from UI OR monthly cron. Runs full training pipeline → generates new run in MLflow → sends notification to platform engineer | P1 |
| JO-03 | **Label ingestion job**: polls Instinct feedback API (or S3/SFTP drop zone) for new chargeback/confirmed fraud labels every 6h. Writes to `fraud_labels` table | P1 |
| JO-04 | **Monitoring computation job**: calculates PSI/KS drift metrics for all active features daily; writes to `feature_drift_log` table | P1 |
| JO-05 | **Shadow score comparison job**: computes champion vs shadow AUC/AP on last 7d traffic daily; writes to `shadow_comparison_log`; surfaces in Model Hub | P1 |
| JO-06 | **Dead letter queue**: failed scoring requests (API 500 errors) are enqueued; retry worker re-scores them and logs result | P2 |
| JO-07 | All DAG run history visible in Pipeline Control dashboard page | P1 |
| JO-08 | Alerting: DAG failures and drift alerts trigger email or webhook notification (configurable endpoint per tenant) | P1 |

---

### 6.7 Model Monitoring & Observability

#### 6.7.1 Performance Monitoring

| ID | Requirement | Priority |
|---|---|---|
| MO-01 | Daily computation of ROC-AUC, PR-AUC, and flagging rate on labelled transactions from last 30d; stored in `model_performance_log` | P1 |
| MO-02 | Score distribution metrics (mean, std, % > 0.5, % > 0.8) computed daily and stored; used for drift detection | P0 |
| MO-03 | P50/P95/P99 API latency exported as Prometheus metrics from FastAPI; scraped by Grafana | P1 |
| MO-04 | Throughput (requests/minute) and error rate exported as Prometheus metrics | P1 |

#### 6.7.2 Feature Drift Detection

| ID | Requirement | Priority |
|---|---|---|
| MO-05 | PSI computed daily for all 41 model features vs training baseline distribution | P1 |
| MO-06 | KS test statistic computed daily for numeric features | P1 |
| MO-07 | Alert threshold: PSI > 0.2 (severe drift) → auto-retrain trigger; PSI 0.1–0.2 (moderate drift) → notification only | P1 |
| MO-08 | Feature freshness alert: if Feast `materialize` has not run within expected cadence × 2, mark feature as stale and include `feature_stale: true` flag in scoring response | P1 |

#### 6.7.3 Explainability & Audit

| ID | Requirement | Priority |
|---|---|---|
| MO-09 | SHAP TreeExplainer runs asynchronously after each prediction for XGBoost/LightGBM/RF; top-5 feature contributions stored in `shap_log` (transaction_id, feature_name, shap_value) | P0 |
| MO-10 | SHAP KernelExplainer for non-tree models (PyTorch NN, autoencoder) — runs in background, higher latency acceptable | P1 |
| MO-11 | Every scored transaction has a permanently queryable audit record: features served, model version, score, SHAP contributions, feature source health flags | P0 |
| MO-12 | Audit export: bulk export of audit records for a date range as CSV/Parquet for regulatory submission | P1 |
| MO-13 | EU AI Act Article 13 transparency report: automated generation of model card per training run (algorithm, training data period, validation metrics, feature list, intended use, limitations) | P2 |

---

### 6.8 Multi-Tenancy & Access Control

| ID | Requirement | Priority |
|---|---|---|
| MT-01 | Each tenant has an isolated Postgres schema: `{tenant_id}.model_score_log`, `{tenant_id}.online_feature_log`, `{tenant_id}.fraud_labels`, `{tenant_id}.feature_drift_log` | P0 |
| MT-02 | Redis keys are prefixed by tenant: `{tenant_id}:fraud:user:{user_id}:txn_ts` | P0 |
| MT-03 | Feast feature views and feature services are tagged by tenant; materialize runs are scoped per tenant | P0 |
| MT-04 | MLflow experiments are namespaced per tenant: experiment name = `{tenant_id}/{model_name}` | P0 |
| MT-05 | API routing: `/api/v1/tenants/{tenant_id}/score` — each tenant gets an isolated scoring path | P0 |
| MT-06 | Platform-level admin role can view all tenants; tenant-level admin can only access their own tenant | P0 |
| MT-07 | Tenant onboarding via admin panel: creates schema, Redis namespace, Feast config, initial API key, default model placeholder | P1 |
| MT-08 | Data residency: tenant data never leaves the configured storage path (no cross-tenant queries possible at the DB layer) | P0 |
| MT-09 | Tenant-level API rate limits configurable; burst allowance configurable | P1 |

---

### 6.9 Installation & Deployment

Two deployment modes are supported. Both are private, self-hosted, with no GBG call-home or SaaS dependency at runtime.

#### Mode A — Cloud / Linux (Docker / Kubernetes)

**Target:** New clients, cloud-hosted, or any Linux server (AWS EC2, Azure VM, on-premise Linux)

| ID | Requirement | Priority |
|---|---|---|
| DP-01 | Single-command bootstrap: `docker compose up` starts all platform services (Postgres, Redis, FastAPI, Prefect, Dashboard, MLflow tracking server) | P0 |
| DP-02 | Production `docker-compose.prod.yml` with: persistent named volumes, resource limits, healthchecks, restart policies | P0 |
| DP-03 | Helm chart for Kubernetes deployment (optional, for clients with k8s clusters) | P2 |
| DP-04 | Platform configuration via a single `.env` file — no code changes required for deployment | P0 |
| DP-05 | Database migrations run automatically on first start via Alembic (Python) or the existing SQL bootstrap pattern | P0 |
| DP-06 | TLS termination via nginx reverse proxy included in docker-compose (self-signed cert for on-premise; Let's Encrypt / ACM for cloud) | P1 |
| DP-07 | Automated daily backup of Postgres and MLflow SQLite to a configurable path (local disk or S3) | P1 |
| DP-08 | Platform versioning: `docker-compose.yml` pins all service image versions; updates applied by pulling new image tags and running migrations | P0 |

#### Mode B — On-Premise Windows Server (MSI Installer)

**Target:** Existing clients currently running Instinct on Windows Server 2019/2022 who cannot move to Docker/Linux

| ID | Requirement | Priority |
|---|---|---|
| DP-09 | MSI installer (WiX 4.x) bundles: Python 3.11 embedded runtime, all Python wheel dependencies (offline install, no internet required), Postgres portable or connects to existing client SQL Server, Redis for Windows binary | P0 |
| DP-10 | Windows Service wrapper for: FastAPI scoring API, Prefect agent, Streamlit dashboard — all managed as Windows Services with EventLog integration | P0 |
| DP-11 | Installer wizard (6 steps): license, install path, database config (new Postgres / existing SQL Server / existing PostgreSQL), Redis config, Instinct integration URL, admin password | P0 |
| DP-12 | Upgrade installer: detects existing installation, backs up database, runs migrations, replaces binaries | P1 |
| DP-13 | Uninstaller: clean removal including database schema drop (with confirmation) | P1 |
| DP-14 | CI pipeline builds MSI artifact on `windows-2022` runner for every release tag | P0 |
| DP-15 | Windows offline wheel cache: all Python dependencies pre-downloaded as `.whl` files and bundled in installer (no pip internet access required during install) | P0 |

#### Platform Software Components

| Component | Cloud Mode | Windows Mode | Notes |
|---|---|---|---|
| **Postgres** | Docker (postgres:15) | Bundled portable or existing | Schema-per-tenant |
| **Redis** | Docker (redis:7-alpine) | Redis for Windows binary bundled | Namespace-per-tenant |
| **FastAPI** | Docker + uvicorn | Windows Service + uvicorn | Multi-worker |
| **Streamlit Dashboard** | Docker | Windows Service | Port 8501 |
| **MLflow Tracking** | Docker (SQLite or Postgres backend) | Windows Service (SQLite) | Port 5000 |
| **Prefect / APScheduler** | Prefect server + agent | APScheduler embedded in Windows Service | Job orchestration |
| **nginx** | Docker | Optional (IIS or standalone nginx for Windows) | Reverse proxy + TLS |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| Requirement | Target |
|---|---|
| Scoring API P95 latency (online path, cached features) | < 20ms |
| Scoring API P95 latency (online path, cache miss) | < 50ms |
| Scoring API P99 latency | < 100ms |
| Scoring API throughput (single node: 4 CPU, 8GB) | ≥ 500 TPS |
| Batch scoring throughput | ≥ 5,000 records/minute |
| Feast materialize (1M records) | < 10 minutes |
| Model training (200k records, XGBoost) | < 5 minutes |

### 7.2 Reliability

| Requirement | Target |
|---|---|
| Scoring API availability (per month) | ≥ 99.5% |
| Recovery time after component restart | < 30 seconds (model pre-loaded) |
| Redis failover (AOF persistence) | No data loss on clean restart |
| Database backup RPO | ≤ 24h (daily backup) |

### 7.3 Security

| Requirement |
|---|
| All API endpoints require JWT Bearer token or shared cookie (Instinct SSO) |
| No secrets committed to source control — all credentials via environment variables or secrets manager |
| Postgres connections over TLS; Redis password-protected |
| SHAP values and model artifacts stored in tenant-scoped paths |
| Audit log of all model promotions (who, when, from which run to which run) |
| Data Protection keys (for cookie encryption) generated at install time, stored outside repository |
| OWASP Top 10 review completed before v1.0 release |

### 7.4 Maintainability

| Requirement |
|---|
| Algorithm plugins follow a `BaseModel` interface — adding a new algorithm requires only creating a new class, not modifying core training pipeline |
| Feature views follow versioning convention — breaking changes increment version, active FeatureService pins to specific versions |
| All configuration in YAML files — no algorithm-specific logic in core routing code |
| CI pipeline runs: unit tests, integration tests (against Docker stack), load test (Locust headless) on every PR |

### 7.5 Compliance

| Requirement |
|---|
| Every scored transaction has a full audit record: request payload hash, feature vector, model version, SHAP top-5, scored_at timestamp |
| Model cards generated per training run (EU AI Act Article 13) |
| Data retention: `model_score_log` and `shap_log` retained for minimum 5 years (configurable per tenant) |
| No personal data stored in feature vectors — entity IDs only (user_id, device_id are pseudonymous) |

---

## 8. Release Milestones

### Phase 0 — Prototype (Current State — Complete ✅)
FastAPI scoring, Redis features, Feast, XGBoost/LightGBM/RF, MLflow, Docker Compose, locust load test.

### Phase 1 — Production Foundation (3 months)
**Goal:** Production-grade serving, multi-tenancy, Instinct integration, operator dashboard MVP.

- MT-01 to MT-06 (multi-tenant Postgres + Redis namespacing)
- SV-01, SV-07, SV-08 (tenant-scoped API, graceful degradation, JWT auth)
- SV-11, SV-12 (Instinct webhook integration)
- IN-01 to IN-05 (Instinct rule variables)
- IN-14 (AppSettings migration from ZeroMQ to ML platform URL)
- DP-01 to DP-05, DP-08 (production Docker Compose)
- UI-01 to UI-07 (Monitoring page)
- UI-08 to UI-15 (Model Hub — promote, rollback)
- UI-17 to UI-22 (Score Explorer)
- FP-09 to FP-13 (online features, pipelining, caching — already in prototype)
- TR-01 to TR-07, TR-10, TR-15 (training pipeline — already in prototype, formalize)
- MO-09, MO-11 (SHAP logging, audit trail)

**Definition of Done:** Two tenants running isolated on the same deployment; Instinct calling ML platform webhook; Operator can see live scores on dashboard; Data scientist can train and promote model via UI without terminal.

### Phase 2 — Advanced ML Capabilities (3 months after Phase 1)
**Goal:** Full ML platform feature set, monitoring, champion-challenger.

- TR-06 (Optuna hyperparameter search)
- TR-08, TR-09 (delayed labels, auto-retraining trigger)
- TR-16 to TR-18 (shadow scoring, A/B, promotion gate)
- TR-20 (rollback via UI)
- JO-01 to JO-08 (Prefect/APScheduler orchestration)
- MO-01 to MO-08 (performance monitoring, PSI/KS drift detection)
- UI-23 to UI-32 (Pipeline Control, Training Hub pages)
- UI-33 to UI-37 (Model Monitoring page)
- FP-14 to FP-16 (cold-start defaults, feature quality checks)
- IN-06 to IN-09 (label feedback loop from Instinct)
- DP-06, DP-07 (nginx TLS, automated backup)
- Neural Network (PyTorch/skorch) and Isolation Forest/Autoencoder algorithm additions

**Definition of Done:** Drift detection fires and triggers auto-retraining; shadow model runs alongside champion; data scientist can compare shadow vs champion in UI and promote.

### Phase 3 — Windows On-Premise & Enterprise (3 months after Phase 2)
**Goal:** Full Windows Server deployment parity; EU AI Act compliance exports; tenant self-service.

- DP-09 to DP-15 (Windows MSI installer, Windows Services)
- MT-07 to MT-09 (tenant onboarding, self-service)
- UI-38 to UI-41 (Tenant Admin page)
- IN-10 to IN-12 (SSO cookie, RBAC)
- IN-13 to IN-15 (legacy migration tooling)
- MO-12, MO-13 (audit export, EU AI Act model card)
- TR-19 (canary deployment)
- SV-02, SV-03 (batch scoring API)
- DP-03 (Helm chart — optional)

**Definition of Done:** Existing Instinct client on Windows Server can install the platform from an MSI without internet access; legacy models can be imported; full audit trail exportable for regulatory review.

---

## 9. Technical Debt & Migration Plan from Legacy

### 9.1 Dependencies to Replace

| Legacy | Replacement | Rationale |
|---|---|---|
| Python 3.8 (EOL) | Python 3.11 | Security, performance (3.11 is 25% faster than 3.8) |
| TensorFlow 2.3 + standalone Keras 2.4 | PyTorch 2.x + skorch | TF/Keras API is broken in any modern pip environment; PyTorch is the dominant research framework with stable scikit-learn interop |
| Flask 1.1.2 + gevent | FastAPI + uvicorn (already in prototype) | Native async, automatic OpenAPI docs, Pydantic validation |
| LightGBM 3.0.0 | LightGBM 4.x | Two major versions of improvements and speed |
| AutoMapper 6.2.2 (C#) | AutoMapper 12.x | 6 major version gap |
| Dapper 1.50.2 (C#) | Dapper 2.x | Security and performance fixes |
| ZeroMQ IPC (C# ↔ Python) | Direct HTTP call from Instinct to FastAPI ML Platform | Eliminates architectural complexity; platform is now a standalone HTTP service |

### 9.2 Security Remediations

| Issue | Remediation |
|---|---|
| JWT secret hardcoded in `appsettings.json` | Injected at deploy time via environment variable / secrets manager |
| Data Protection key committed to repository (`key-ac29d979-...xml`) | Generated during installer wizard; stored outside repository |
| SSL certificates committed (`localhost.pfx`, `localhost.key`) | Remove from repository; generate during install |
| `JwtTokenSecret: "fN00qrzuSM_NUWKQ2ZBbIkQoJI8wAE-gUFihC8F9wjo"` | Rotate immediately; remove from `appsettings.json` |

### 9.3 What to Preserve from Legacy

| Legacy Component | Preserve As |
|---|---|
| Hangfire job scheduler | Replaced by Prefect (cloud) / APScheduler (on-premise); same capability, better UI and monitoring |
| Multi-DB routing (SQL Server / MySQL / PostgreSQL) | SQLAlchemy engine factory in Python — same DB flexibility, language-native |
| Custom plugin system (`PluginsWatcher.py`) | `BaseModel` Python interface — plugins drop into `plugins/` directory, discovered at training startup; hot-reload preserved |
| Windows MSI installer (WiX) | Upgraded to WiX 4.x in Phase 3; same installation UX for existing clients |
| SignalR real-time training progress | Replaced by Streamlit `st.status` streaming log in Training Hub page |
| Cookie SSO with Instinct | Preserved as `.AspNet.SharedCookie` pattern in Phase 1, upgraded to JWT in Phase 1.1 |
| Localization (`.resx` files) | Out of scope for v1.0; add to v2.0 roadmap |

---

## 10. Open Questions & Decisions

| # | Question | Options | Recommended | Owner |
|---|---|---|---|---|
| OQ-01 | Offline store for cloud mode: DuckDB → Postgres or S3 Parquet? | (A) Postgres with Timescale extension (B) S3 Parquet + DuckDB httpfs (C) Keep local DuckDB for single-node | (B) S3 Parquet for cloud — scales horizontally, decouples compute and storage; (C) for on-premise single-node | Architecture |
| OQ-02 | Orchestrator: Prefect vs Airflow? | (A) Prefect — simpler setup, Python-native, built-in UI (B) Airflow — more established, wider ecosystem | (A) Prefect for v1.0 — faster to ship; Airflow available as option in Phase 3 | Platform Eng |
| OQ-03 | MLflow backend for multi-tenant: SQLite per tenant vs shared Postgres? | (A) One SQLite per tenant (B) Shared Postgres MLflow tracking server (namespaced by experiment) | (B) Postgres MLflow for production — SQLite has concurrency issues with parallel training runs | Architecture |
| OQ-04 | Neural network framework: PyTorch + skorch vs keep TensorFlow? | (A) PyTorch + skorch — modern, stable, sklearn-compatible (B) TensorFlow 2.x — upgrade path available but breaking changes from Keras standalone | (A) PyTorch — lower maintenance burden, better long-term ecosystem trajectory | ML Eng |
| OQ-05 | Kafka for cloud mode vs direct Redis writes? | (A) Kafka (Redpanda) — event replay, exactly-once, decoupled producers (B) Direct Redis write — simpler, lower latency, no broker to manage | (A) Kafka for cloud (production-grade event sourcing); (B) direct write for single-node on-premise | Architecture |
| OQ-06 | Streamlit vs custom React UI? | (A) Streamlit — Python-native, fast to ship, limited customisation (B) React + FastAPI — full control, higher build cost | (A) Streamlit for v1.0 — ship to clients faster; plan migration to React post-Phase 2 | Product |
| OQ-07 | SHAP computation: sync (adds latency) vs async (delay in UI)? | (A) Synchronous — score + SHAP in one response (~5-15ms overhead for tree models) (B) Asynchronous — SHAP computed in background, available in audit log within 1s | (B) Async for v1.0 — keeps P95 latency target achievable; Instinct only needs score, not SHAP in real-time | ML Eng |
| OQ-08 | Windows mode: bundle portable Postgres vs require existing SQL Server? | (A) Bundle portable Postgres — zero pre-requisites for client (B) Connect to existing SQL Server — consistent with Instinct's existing DB | (A) Bundle Postgres by default with option (B) for clients who already run SQL Server for Instinct | Product |

---

*Document maintained under: `Feature Store Experimentation/fraud-realtime-ml-prototype/markdown/PRD_fraud_realtime_ml_platform.md`*  
*Next review: Phase 1 kickoff*
