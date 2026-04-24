# Plan: End-to-End ML Platform Roadmap

## Context: What's Already Built (Phase 0 — Prototype)

| Component | Status | Notes |
|---|---|---|
| PostgreSQL (raw ops) | ✅ | OLTP source, score log |
| DuckDB (offline store) | ✅ | Local file, single writer |
| Redis (online store) | ✅ | Sliding-window counters + batch materialization |
| dbt feature engineering | ✅ | user/device/merchant fct_* tables |
| Feast feature registry | ✅ | Local SQLite registry, FileSource → Parquet |
| Feature versioning (naming) | ✅ | *_fv_v<N> convention |
| FastAPI /score endpoint | ✅ | Async, gunicorn multi-worker |
| Async DB logging | ✅ | asyncpg + asyncio.Queue |
| XGBoost/LightGBM/RF training | ✅ | Config-driven YAML experiments |
| MLflow tracking | ✅ | mlflow.db, model_meta.json |
| Model promotion script | ✅ | scripts/promote_model.py |
| Locust load testing | ✅ | Headless + UI mode |
| Stream simulator (in-process) | ✅ | stream_transactions.py |
| Health endpoint | ✅ | /health |
| Model calibration | ✅ | sigmoid/isotonic/beta |

## Missing across all 3 pillars

### Pillar 1 Gaps: Feature Platform
- No multi-tenancy (single Postgres DB, single Redis namespace, shared local DuckDB)
- No self-service feature UI / catalog
- No automated cron/orchestrator for offline pipeline (currently manual Makefile)
- No Kafka/event bus for real-time feature ingestion (in-process only)
- No shared Feast registry (local SQLite, not multi-team)
- No cloud-native storage (local Parquet, can't share across teams)
- No feature quality monitoring (freshness, null rates, drift alerts)
- No feature lineage tracking
- No end-to-end pipeline progress visibility (DAG view)
- No backfill support

### Pillar 2 Gaps: API Management
- No multi-model/multi-use-case routing (single hardcoded /score path)
- No API lifecycle management (create/deploy/deprecate)
- No safe API deployment (blue/green, canary)
- No Grafana dashboard (no metrics export)
- No centralized API registry/gateway
- Stream simulator is CLI-only, no UI
- Load testing UI exists (Locust) but needs proper integration/UX

### Pillar 3 Gaps: Training Hub
- No parallel experiment runner (sequential only)
- No hyperparameter sweep (no Optuna/Ray Tune)
- No champion-challenger framework with traffic routing
- No canary deployment / shadow testing
- No temporal model monitoring dashboard
- No model explainability (SHAP/LIME)
- No auto-retrain trigger (drift-based or scheduled)
- No concept drift detection
- No feedback loop for delayed labels (chargeback latency)
- Model registry incomplete (MLflow partial, no promotion UI)

---

## Platform Roadmap

### Phase 1 — Infrastructure Foundation for Scale (Months 1–2)
Multi-tenancy & shared state. Pre-requisite for everything else.

1.1 Introduce Kafka as event bus — decouple stream_transactions.py from in-process updater.py. Producer → Kafka topic `raw.transactions.{tenant_id}` → Consumer service updates Redis.
1.2 Migrate offline storage to object storage (S3/GCS). DuckDB reads/writes S3 via httpfs. Parquet on S3. Feast FileSource → S3FileSource. No more local .duckdb file.
1.3 Migrate Feast registry from local SQLite → shared registry (S3 or Postgres-backed). Multi-team read/write.
1.4 Introduce tenant/team namespacing:
    - Redis key prefix `{tenant_id}:{feature_name}`
    - Postgres schema-per-tenant for model_score_log, online_feature_log
    - dbt project-per-domain profiles
1.5 Containerize everything remaining: add Kafka (Redpanda), Zookeeper/KRaft, MinIO (local S3 substitute) to docker-compose.yml.
1.6 Introduce Airflow (or Prefect) as pipeline orchestrator. Replace Makefile sequential steps with DAGs. First DAG: offline_pipeline (export → dbt → feast_apply → materialize).

**Relevant files**: docker-compose.yml, scripts/materialize_features.py, feast_repo/feature_repo/feature_store.yaml, feast_repo/feature_repo/data_sources.py, app/online_features/updater.py, simulator/stream_transactions.py

**Verification**: Multi-tenant end-to-end test — two tenants (fraud team, marketing team) run offline pipelines independently without conflict. Kafka consumer updates Redis with correct namespace prefix.

---

### Phase 2 — Feature Platform (Months 2–4)
Self-service feature management for DS teams across domains.

2.1 Feature Catalog Service (backend API):
    - CRUD: register new feature view, list features, deprecate feature version
    - Stores metadata: owner team, domain (fraud/marketing/credit), entity type, refresh cadence, freshness SLA
    - Backed by Postgres `feature_catalog` table
    - Wraps Feast `feast apply` on registration

2.2 Feature Discovery UI (React/Streamlit):
    - Browse features by team/domain/entity
    - View feature schema, lineage (which dbt model produces it), version history
    - "Promote to online" button triggers Feast materialize job
    - Freshness status badge (last materialized timestamp vs SLA)

2.3 Automated Pipeline Scheduler (Airflow DAGs):
    - Daily DAG: export_pg_to_duckdb → dbt_run → materialize (per tenant)
    - Weekly DAG: full dbt test suite + data quality checks
    - Monthly DAG: full historical backfill (configurable window)
    - Parameterized by `tenant_id`, `domain`, `target_date`
    - DAG progress visible in Airflow UI (replaces "single run-pipeline" requirement)

2.4 Feature Quality Monitoring:
    - dbt tests already exist — extend with custom tests: null rate threshold, distribution shift (KS test), row count bounds
    - Export test results to Postgres `feature_quality_log` table
    - Grafana panel: feature freshness, null rates, p50/p95 value distributions per feature

2.5 Feature Versioning Governance:
    - Current: naming convention only (*_fv_v<N>)
    - Add: version manifest in Feast registry metadata (breaking vs non-breaking flag)
    - Add: deprecation workflow — mark old version, consumers get warning, hard delete after 30-day grace period

2.6 Point-in-time Backfill Tool:
    - CLI: `python scripts/backfill_features.py --tenant fraud --start 2025-01-01 --end 2025-12-31`
    - Runs DuckDB compute for historical window, exports partitioned Parquet, materializes into Redis

2.7 Unsupervised Feature Support:
    - Add new entity type support beyond user/device/merchant (e.g. ip_address, session_id)
    - Add feature views for clustering inputs: `customer_segment_fv_v1` (RFM features for marketing)
    - Add feature views for anomaly detection: `user_behavioral_baseline_fv_v1`

**Relevant files**: dbt_project/, feast_repo/feature_repo/, scripts/, Makefile, docker-compose.yml
**New files**: services/feature_catalog/ (FastAPI backend), ui/feature_hub/ (frontend), dags/ (Airflow DAGs)

---

### Phase 3 — API Management Platform (Months 3–5)
Multi-model, multi-team API lifecycle.

3.1 Dynamic Model Router:
    - Replace hardcoded `/score` with `/score/{model_slug}` pattern
    - Model slug maps to (model artifact path, feature service version, preprocessing artifact)
    - Router config stored in Postgres `model_routing` table: slug → active run_id/alias
    - app/scoring.py: resolve model slug → load correct model + meta at request time

3.2 API Registry Service:
    - CRUD API for scoring endpoints: create new path, list active paths, deprecate path
    - Each registration requires: model_slug, feature_service_version, owner_team, use_case
    - Validation gate: check model exists in MLflow registry + feature service is materialized before activating

3.3 Safe API Deployment (Blue/Green + Canary):
    - Extend model_routing table with: `traffic_split` (0–100%), `shadow_mode` (bool)
    - FastAPI middleware reads routing config per request: route X% to challenger, 100-X% to champion
    - Shadow mode: score with challenger model but return champion's response (log both)
    - Blue/green: swap champion atomically (no downtime) via `make promote-model`

3.4 Grafana Dashboard — Service Monitoring:
    - Export metrics from FastAPI via prometheus-fastapi-instrumentator
    - Metrics: request rate, p50/p95/p99 latency, error rate, per-model-slug breakdown
    - Grafana panels: TPS live, latency percentiles, fraud score distribution over time, model version active
    - Add Grafana + Prometheus to docker-compose.yml

3.5 Stream Simulator UI:
    - Streamlit or React app wrapping stream_transactions.py
    - Controls: events/sec slider, fraud rate slider, tenant selector, start/stop button
    - Live event feed display (last 50 events, color-coded by fraud flag)
    - Real-time Redis stats panel (key counts, memory usage)

3.6 Load Testing UI Integration:
    - Locust UI already exists (locustfile.py + `make load-test-ui`)
    - Add pre-built test scenarios: ramp-up test, soak test, spike test
    - Auto-generate TPS report card (p50/p99/max, error rate, TPS ceiling)
    - Link from API Management UI: "Run Load Test" button against selected model slug

**Relevant files**: app/main.py, app/scoring.py, app/model_loader.py, app/schemas.py, locustfile.py, simulator/stream_transactions.py, docker-compose.yml
**New files**: app/router.py, services/api_registry/, ui/api_management/, grafana/dashboards/

---

### Phase 4 — ML Training Hub & Model Management (Months 4–7)
Full MLOps lifecycle with automation.

4.1 Parallel Experiment Runner:
    - CLI + UI: submit N training configs in parallel, each gets own MLflow run
    - Implementation: Python multiprocessing pool or Celery workers, each calls train_model.py with different config
    - Configs discovered from training/experiments/*.yaml glob
    - Live progress: poll MLflow API for run status, stream to UI

4.2 Hyperparameter Sweep:
    - Integrate Optuna for Bayesian search; Ray Tune as optional distributed backend
    - Add `sweep` section to training YAML config: define search space per param
    - Sweep results go into same MLflow experiment, best trial promoted automatically
    - `make sweep CONFIG=training/experiments/xgboost_v1.yaml N_TRIALS=50`

4.3 Champion-Challenger Framework:
    - Extend model_routing (Phase 3.1) with challenger_run_id + traffic_split
    - `make promote-challenger RUN_ID=<id> SPLIT=10` → routes 10% traffic to challenger
    - Comparison dashboard: champion vs challenger on KPIs (precision, recall, score distribution, business metrics if available)
    - Auto-promotion: if challenger KPIs exceed champion for N consecutive hours → promote

4.4 Canary Deployment:
    - Staged rollout: 1% → 5% → 10% → 50% → 100% traffic over configurable time windows
    - Rollback trigger: if error rate or p99 latency threshold breached → auto-revert to 0% canary
    - Managed via `model_routing` table + Airflow DAG: `canary_promotion_dag`

4.5 Shadow Testing:
    - Shadow mode already partially designed (Phase 3.3)
    - Add shadow scoring log: `shadow_score_log` table in Postgres (shadow_run_id, transaction_id, shadow_score, champion_score, timestamp)
    - Shadow analysis notebook: compare distributions, lift analysis, disagreement rate

4.6 Model Monitoring Dashboard:
    - Scheduled Airflow DAG: daily model monitoring job
    - Metrics computed: PSI (Population Stability Index) on input features, score distribution shift, precision/recall on labeled feedback window
    - Grafana panels: PSI over time per feature, score percentile bands over time, feature importance stability
    - Alert rules: PSI > 0.2 → "feature drift warning"; PR-AUC drop > 5pp → "model degradation alert"

4.7 Model Explainability:
    - Add SHAP computation to evaluate_model.py: global feature importance (beeswarm), local explanation per prediction
    - Serve per-prediction SHAP values via `/explain/{transaction_id}` endpoint
    - Explainability report in MLflow artifacts (SHAP summary plot, dependence plots)
    - Integration with shadow log: flag high-disagreement predictions for SHAP comparison

4.8 Auto-Retrain Trigger:
    - Two trigger modes:
      a) Scheduled: Airflow DAG `weekly_retrain_dag` — runs every Monday, trains on last 90-day window
      b) Drift-triggered: model monitoring DAG publishes to Kafka topic `alerts.model_drift`; consumer DAG auto-triggers retrain
    - Retrain uses latest training_config.yaml (or best config from last sweep)
    - New model auto-registered in MLflow, enters champion-challenger flow (not auto-promoted)

4.9 Model Registry & Promotion UI:
    - MLflow already provides run tracking + model registry
    - Build thin UI layer on top of MLflow REST API: list models, compare metrics, 1-click promote to challenger/champion
    - Promotion gates: requires minimum ROC-AUC threshold (configurable), human approval step for production
    - Model cards: auto-generated markdown with training data window, feature list, eval metrics, calibration curve

4.10 Feedback Loop (Delayed Labels):
    - New Airflow DAG: `label_ingestion_dag` — daily, pulls chargebacks/disputes from Postgres into `fraud_labels`
    - Supports delayed label arrival (configurable lag window, default 30 days)
    - Re-scores historical transactions with new labels for monitoring accuracy
    - Triggers drift alert if newly labeled data shows significant pattern shift

**Relevant files**: training/train_model.py, training/evaluate_model.py, training/experiments/*.yaml, app/main.py, app/scoring.py, scripts/promote_model.py, Makefile
**New files**: training/sweep.py, training/parallel_runner.py, app/explainer.py, dags/retrain_dag.py, dags/monitoring_dag.py, dags/canary_dag.py, ui/training_hub/

---

### Phase 5 — Platform Hardening & Governance (Months 7–9)
Production-grade reliability, security, and observability.

5.1 API Gateway (Kong or AWS API Gateway): rate limiting, auth (JWT/API keys), request routing, audit log
5.2 Role-Based Access Control: team-scoped feature/model access (fraud team can't overwrite marketing features)
5.3 Data Contract Enforcement: Great Expectations or dbt tests as hard gates in pipeline DAGs
5.4 Disaster Recovery: Redis AOF persistence, Postgres WAL archiving, model artifact S3 versioning
5.5 Cost Attribution: per-team compute/storage cost tracking (useful for chargeback to business units)
5.6 Platform SLOs: define and monitor: feature freshness SLO (<24h for daily features), API p99 SLO (<100ms), pipeline completion SLO (<4h for daily run)

---

## Technology Decisions

| Component | Current | Platform Target |
|---|---|---|
| Offline storage | Local DuckDB file | DuckDB → S3/MinIO (httpfs) |
| Feature registry | Local SQLite | Postgres-backed or S3 |
| Event bus | In-process function call | Kafka (Redpanda for local) |
| Pipeline orchestration | Makefile | Apache Airflow (or Prefect) |
| Feature catalog | None | Custom FastAPI + Postgres |
| API gateway | None | Kong or Traefik |
| Metrics | None | Prometheus + Grafana |
| Hyperparam sweep | None | Optuna |
| Model explainability | None | SHAP |
| Drift detection | None | PSI + KS tests, custom DAG |
| Multi-tenancy | None | Redis namespace + Postgres schemas |
| Container orchestration | Docker Compose | Docker Compose (dev) → Kubernetes (prod) |

## Sequencing Logic

Phases 1 and 3.4 (Grafana/Prometheus) can start in parallel.
Phase 2 depends on Phase 1 (shared registry, S3 storage).
Phase 3 depends on Phase 1 (Kafka) and can start parallel with Phase 2.
Phase 4 depends on Phase 3 (champion-challenger routing, shadow log).
Phase 5 runs alongside Phase 4 final hardening.
Phase 6 (Deployment Packaging) runs alongside Phase 5 — packaging what's built in Phases 1–5 for both SaaS and on-prem distribution.

---

## Deployment Architecture: Two Platform Scenarios

### Core Design Principle: Control Plane / Data Plane Separation

Every service is split into two logical tiers:
- **Control Plane**: tenant config, routing table, feature registry, model registry, monitoring aggregation, billing metering — runs centrally (SaaS) or per-client (on-prem).
- **Data Plane**: ML scoring (FastAPI), feature serving (Redis), offline pipeline (DuckDB + dbt), stream ingestion (Kafka consumer) — runs closest to data and traffic.

This separation is the enabler for both deployment modes. In SaaS, FraudFighter operates the control plane and shared data plane. In on-prem, the entire stack ships to the client's environment.

---

### Scenario 1 — SaaS: FraudFighter-Hosted

**Architecture**: Shared control plane, isolated data plane per tenant (or per tier).

```
                    ┌─────────────────────────────────────────────┐
                    │          FraudFighter SaaS                  │
                    │                                             │
   Client A ───────►│  API Gateway (Kong)                         │
   Client B ───────►│    └── /score/{tenant_id}/{model_slug}      │
   Client C ───────►│                                             │
                    │  Control Plane (shared)                     │
                    │    ├── Tenant Mgmt Service                  │
                    │    ├── Model Registry (MLflow + Postgres)   │
                    │    ├── Feature Catalog Service              │
                    │    ├── API Registry Service                 │
                    │    ├── Billing / Usage Metering             │
                    │    └── Airflow (multi-tenant DAGs)          │
                    │                                             │
                    │  Data Plane per Tenant (isolated)           │
                    │    ├── Redis (namespace or dedicated)        │
                    │    ├── DuckDB → MinIO/S3 per tenant bucket  │
                    │    ├── Kafka topic namespace                │
                    │    └── Postgres schema-per-tenant           │
                    └─────────────────────────────────────────────┘
```

**SaaS-specific additions (Phase 6A)**:

6A.1 Tenant Onboarding Service — REST API + web portal: create tenant, provision namespaced Redis prefix, Postgres schema, S3 bucket prefix, Kafka topic, API key. Single admin action, fully automated.

6A.2 Tenant Tiers:
  - *Starter*: shared Redis + shared Kafka namespace + shared compute, rate-limited scoring
  - *Business*: dedicated Redis instance + dedicated Kafka consumer group + isolated DuckDB compute
  - *Enterprise*: VPC peering option, dedicated worker nodes, custom SLA, private Airflow DAG namespace

6A.3 Usage Metering & Billing:
  - Instrument FastAPI scoring endpoint: count API calls per `tenant_id` + `model_slug`
  - Prometheus metric: `fraud_fighter_score_requests_total{tenant_id, model_slug, tier}`
  - Billing DAG: daily aggregation job writes usage to `billing_usage_log` table
  - Export to Stripe/billing platform via webhook

6A.4 Tenant Admin Portal (web UI):
  - Dashboard: API call volume, model performance over time, active model slug, last feature refresh
  - Self-service: rotate API key, view/manage team members, upgrade tier
  - Feature Hub and Training Hub UIs scoped to tenant

6A.5 Isolation enforcement at API Gateway (Kong):
  - JWT or API key → tenant_id claim
  - Kong plugin injects `X-Tenant-ID` header into every downstream request
  - FastAPI reads `X-Tenant-ID` from header — no client can impersonate another tenant

6A.6 SaaS SLOs + Status Page:
  - Uptime monitoring per region
  - Status page (Statuspage.io or self-hosted Cachet)
  - Incident runbooks for: Redis eviction spike, feature staleness breach, model load failure

---

### Scenario 2 — On-Prem / Exclusive Enterprise

**Architecture**: Full stack ships to client environment. Server-agnostic. No cloud provider lock-in.

```
  Client's environment (bare-metal OR private cloud)
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │  FraudFighter Platform (self-contained)                  │
  │                                                          │
  │  ┌──────────────┐   ┌──────────────┐   ┌─────────────┐  │
  │  │ Docker       │   │ Kubernetes   │   │ Windows     │  │
  │  │ Compose      │   │ Helm Chart   │   │ Installer   │  │
  │  │ (bare-metal) │   │ (private K8s)│   │ (WSL2-based)│  │
  │  └──────────────┘   └──────────────┘   └─────────────┘  │
  │                                                          │
  │  Services (same container images in all modes):          │
  │    FastAPI scoring │ Redpanda (Kafka) │ Redis            │
  │    Postgres        │ MinIO (S3)       │ Airflow          │
  │    Grafana         │ Prometheus       │ MLflow           │
  │    Feature Hub UI  │ Training Hub UI  │ Admin Portal     │
  │                                                          │
  │  Cloud connectors (optional, client-controlled):         │
  │    GCS / Azure Blob / AWS S3 → can replace MinIO         │
  │    Cloud SQL / RDS / Cloud Spanner → can replace Postgres│
  └──────────────────────────────────────────────────────────┘
```

**On-prem-specific additions (Phase 6B)**:

6B.1 Server-Agnostic Storage Abstraction Layer:
  - Introduce `storage_backend` config in `platform.yaml` (single platform config file):
    ```yaml
    storage:
      backend: minio          # minio | s3 | gcs | azure_blob | local
      endpoint: http://minio:9000
      bucket: fraudfighter
    ```
  - All scripts that currently use `S3FileSource` / boto3 go through a thin `StorageClient` adapter
  - Feast `data_sources.py` reads endpoint from `platform.yaml`, not from env var directly
  - DuckDB httpfs configured via same adapter (MinIO uses S3-compatible API natively)

6B.2 Kubernetes Helm Chart (`helm/fraudfighter/`):
  - One chart, all services as subcharts: postgres, redis, redpanda, minio, airflow, mlflow, fastapi, grafana, prometheus
  - `values.yaml` has sane defaults for a single-node install
  - `values-ha.yaml` for multi-replica production setup
  - Works against: GKE, AKS, EKS, OpenShift, bare-metal K8s (kubeadm/k3s/rancher)
  - `helm install fraudfighter ./helm/fraudfighter -f values.yaml`

6B.3 Docker Compose Profile for Bare-Metal:
  - Extend existing `docker-compose.yml` with profiles: `core`, `monitoring`, `training`, `full`
  - `docker compose --profile full up -d` starts everything
  - Separate `docker-compose.windows.yml` override: removes Linux-specific volume mounts, uses Windows-compatible paths, uses Docker Desktop named volumes
  - All images hosted in FraudFighter's private registry (no Docker Hub dependency for air-gapped installs)

6B.4 Windows Bare-Metal Support:
  - Prerequisites: Docker Desktop (WSL2 backend) OR Podman Desktop
  - Provide `install.ps1` PowerShell script: checks prerequisites, pulls images, initializes volumes, writes `.env`, runs bootstrap SQL
  - All Makefile targets mirrored as PowerShell `Invoke-FraudFighter` cmdlets (or `just` cross-platform task runner as Makefile alternative)
  - Volume paths use Docker-managed named volumes (not host-path mounts) to avoid Windows path issues

6B.5 Air-Gapped / Offline Install Support:
  - `make package-images` → exports all container images to `dist/images.tar.gz`
  - `make package-models` → exports trained model artifacts + MLflow metadata to `dist/models.tar.gz`
  - `install.sh --offline --image-bundle dist/images.tar.gz` → loads images from tarball, no internet needed
  - All Python dependencies pre-bundled in base images (no pip install at runtime)

6B.6 Single `platform.yaml` Config File:
  - Root-level config: deployment mode, storage backend, tenant name, license key, SMTP config, auth backend
  - Replaces scattered `.env` + `feature_store.yaml` + `profiles.yml` for client installs
  - Bootstrap script (`scripts/bootstrap_platform.py`) reads `platform.yaml` → writes all derived configs
  - On-prem clients edit ONE file, run one command

6B.7 License Key Enforcement:
  - Embedded license validator: checks license key at startup (offline-capable — no phone-home required for air-gapped)
  - License encodes: max_tenants, max_models_in_registry, expiry_date, allowed_features (e.g. "training_hub", "api_management")
  - FastAPI startup event validates license; logs warning if approaching expiry (30 days)

6B.8 Private Cloud Connector Configs:
  - GCP: `storage.backend: gcs`, uses `google-cloud-storage` SDK. Postgres → Cloud SQL (same psycopg2 driver, just different host). Redis → Memorystore.
  - AWS: `storage.backend: s3`, uses `boto3`. Postgres → RDS. Redis → ElastiCache.
  - Azure: `storage.backend: azure_blob`, uses `azure-storage-blob`. Postgres → Azure Database for PostgreSQL. Redis → Azure Cache for Redis.
  - Each connector is an optional extras install: `pip install fraudfighter[gcp]`, `pip install fraudfighter[aws]`

---

### Phase 6 — Deployment Packaging & Distribution (Months 8–10, parallel with Phase 5)

*Packages everything built in Phases 1–5 for both distribution channels.*

**6A — SaaS packaging** (run by FraudFighter):
- Tenant onboarding automation
- Usage metering + billing pipeline
- Tenant admin portal
- Kong gateway with tenant JWT enforcement
- Multi-region deployment (optional): data plane per region, shared control plane

**6B — On-prem packaging** (shipped to client):
- `platform.yaml` single config
- `StorageClient` abstraction layer
- Helm chart
- Docker Compose with profiles + Windows override
- `install.sh` + `install.ps1` bootstrap scripts
- Air-gapped image bundle
- License key system
- Private cloud connector extras

**Shared work** (same codebase, different config):
- Container images are identical; only `platform.yaml` differs
- CI/CD pipeline builds one set of images, pushes to FraudFighter private registry
- Versioned releases: `fraudfighter:2.1.0` tags applied to all images simultaneously

---

## Updated Technology Stack

| Component | Prototype | SaaS | On-Prem |
|---|---|---|---|
| Offline storage | Local DuckDB file | DuckDB + S3 (AWS) | DuckDB + MinIO / GCS / Azure Blob |
| Feature registry | Local SQLite | Postgres-backed (shared) | Postgres-backed (client-owned) |
| Event bus | In-process | Managed Kafka (MSK/Confluent) | Redpanda (self-contained) |
| Pipeline orchestration | Makefile | Managed Airflow (MWAA/Cloud Composer) | Airflow on K8s or bare-metal |
| Object storage | None | AWS S3 | MinIO (or GCS/Azure/S3 via connector) |
| API gateway | None | Kong (centralized, FraudFighter-managed) | Kong (per-install) or Traefik |
| Auth | None | JWT + API key (Kong plugin) | LDAP/AD integration or API key |
| Container runtime | Docker Compose | Kubernetes (EKS/GKE) | K8s (any) or Docker Compose |
| Metrics | None | Prometheus + Grafana (managed) | Prometheus + Grafana (self-hosted) |
| Install method | Manual Makefile | Terraform + GitOps (ArgoCD) | `install.sh` / `install.ps1` / Helm |

---

## Updated Sequencing

```
Months 1–2:   Phase 1 (Infrastructure Foundation — Kafka, S3/MinIO, Airflow, multi-tenancy)
Months 2–4:   Phase 2 (Feature Platform) + Phase 3 start (API Management)
Months 3–5:   Phase 3 (API Management) — parallel with Phase 2
Months 4–7:   Phase 4 (Training Hub)
Months 7–9:   Phase 5 (Hardening & Governance)
Months 8–10:  Phase 6 (Deployment Packaging — SaaS + On-prem)
              Phase 6A and 6B run in parallel after Phase 5 core services are stable
```

