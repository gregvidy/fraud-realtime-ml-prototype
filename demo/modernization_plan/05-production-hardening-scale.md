# Phase 5 — Production Hardening & Scale Validation

> **Duration**: 4-6 weeks
> **From**: Phase 8 (Performance & Scale Testing) + additional production readiness items
> **Goal**: Validate 2K TPS SLA, security hardening, CI/CD, deployment automation, hybrid deployment support
> **Prerequisite**: Phase 4 complete (full ML lifecycle operational)

---

## 5.1 What This Phase Delivers

By the end of Phase 5, FraudML is:
- **SLA-validated**: 2,000+ TPS sustained with < 50ms P50 and < 200ms P99, proven under stress
- **Security-hardened**: No pickle, secrets managed, RBAC, TLS everywhere, vulnerability-scanned
- **CI/CD automated**: Build → test → push image → deploy (staging → prod) pipeline
- **Deployment-flexible**: Docker Compose (on-prem single-server), K8s Helm charts (cloud/multi-node)
- **Operations-ready**: Runbooks, SLA dashboards, capacity planning, disaster recovery tested
- **Multi-tenant validated**: Isolated tenant data, per-tenant model lifecycle, shared infrastructure

---

## 5.2 Architecture (Final State)

```
┌──────────────────────────────────── FINAL ARCHITECTURE ─────────────────────────────────────┐
│                                                                                              │
│  ┌─ Predator Platform Integration ──────────────────────────────────────────────────────┐   │
│  │                                                                                      │   │
│  │  Bank Channels → API Gateway → Orchestrator → Rules SDK                              │   │
│  │                       │                          │                                    │   │
│  │                       │ /score (routing)         │ (training trigger)                 │   │
│  │                       ▼                          ▼                                    │   │
│  │              ┌─ FraudML Service ──────────────────────────────────────────────────┐   │   │
│  │              │                                                                    │   │   │
│  │              │  ┌─ Scoring Pool ──────┐  ┌─ Training Service ──┐  ┌─ Monitoring ┐│   │   │
│  │              │  │ N × FastAPI workers │  │ Ray cluster         │  │ Evidently   ││   │   │
│  │              │  │ 2,000+ TPS          │  │ 100M+ rows          │  │ Drift + PSI ││   │   │
│  │              │  │ < 50ms P50          │  │ Multi-model sweep   │  │ Auto-alerts ││   │   │
│  │              │  └────────┬────────────┘  └──────┬──────────────┘  └──────┬───────┘│   │   │
│  │              │           │                      │                        │         │   │   │
│  │              │  ┌────────┴──────────────────────┴────────────────────────┴──────┐  │   │   │
│  │              │  │  Feature Registry (YAML DSL)                                  │  │   │   │
│  │              │  │  41 features × 3 modes (batch + streaming + request)          │  │   │   │
│  │              │  └───────────────────────────────────────────────────────────────┘  │   │   │
│  │              │                                                                    │   │   │
│  │              └────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                              │
│  ┌─ Data Layer ─────────────────────────────────────────────────────────────────────────┐   │
│  │                                                                                      │   │
│  │  ┌─ Streaming ──────────────────────────────────────────────────────────────────┐   │   │
│  │  │  Source DB → Debezium → Kafka → ksqlDB → Redis (velocity) + ScyllaDB       │   │   │
│  │  └──────────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                      │   │
│  │  ┌─ Batch ──────────┐  ┌─ Online ────────────┐  ┌─ Metadata ──────────────────┐   │   │
│  │  │  ClickHouse /     │  │  Redis (hot)         │  │  PostgreSQL                 │   │   │
│  │  │  Snowflake        │  │  ScyllaDB (durable)  │  │  ├ model_registry           │   │   │
│  │  │  + dbt models     │  │  (dual-read)         │  │  ├ score_log (partitioned)  │   │   │
│  │  └──────────────────┘  └──────────────────────┘  │  ├ monitoring_snapshot       │   │   │
│  │                                                   │  └ tenant_config             │   │   │
│  │                                                   └─────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                              │
│  ┌─ Observability ──────────────────────────────────────────────────────────────────────┐   │
│  │  Prometheus + Grafana (metrics) │ ELK (logs) │ Evidently (ML drift) │ APM (traces)  │   │
│  └──────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5.3 Work Breakdown

### 5.3.1 Performance & Scale Validation

| Task | Description | Days |
|------|-------------|------|
| **Load test suite (Locust)** | Comprehensive Locust scripts: (1) sustained 2K TPS for 10 min, (2) burst 5× spike (10K TPS for 30s), (3) ramp-up from 0 → 3K TPS, (4) long-duration soak (2K TPS for 1 hour). | 3 |
| **Multi-entity distribution** | Locust: realistic user/device/merchant distribution (Zipf). Not uniform random — some entities are hot (many txns), most are cold. | 1 |
| **Redis-only benchmark** | Test with ScyllaDB disabled. Measure pure Redis performance ceiling. | 0.5 |
| **Redis + ScyllaDB benchmark** | Test with dual-store. Measure fallback overhead. | 0.5 |
| **ScyllaDB-only benchmark** | Test with Redis disabled (disaster recovery scenario). Measure degraded-mode performance. | 0.5 |
| **Streaming feature latency** | Measure end-to-end: source DB INSERT → Debezium → Kafka → ksqlDB → Redis. Target < 5s. | 1 |
| **Connection pool tuning** | Iterate on Redis pool size, asyncpg pool size, ScyllaDB pool size. Find optimal values for 2K TPS. | 2 |
| **Worker count optimization** | Test: 2/3/4/5 scoring replicas × 2/4/6 workers each. Find optimal configuration. | 1 |
| **Results documentation** | Performance test report: methodology, results, bottleneck analysis, capacity planning recommendations. | 2 |

**Subtotal: ~11.5 days**

#### Load Test Scenarios

```python
# tests/load/locustfile.py
from locust import HttpUser, task, between, constant_pacing
import random
import numpy as np

# Zipf distribution for realistic entity access patterns
user_ids = [f"u_{i:06d}" for i in range(100_000)]
device_ids = [f"d_{i:07d}" for i in range(50_000)]
merchant_ids = [f"m_{i:05d}" for i in range(10_000)]

# Zipf: 20% of users generate 80% of traffic
user_weights = np.random.zipf(1.5, len(user_ids)).astype(float)
user_weights /= user_weights.sum()

class FraudScoringUser(HttpUser):
    wait_time = constant_pacing(1)  # 1 request per second per user
    
    @task(weight=95)
    def score_transaction(self):
        user_id = np.random.choice(user_ids, p=user_weights)
        device_id = random.choice(device_ids)
        merchant_id = random.choice(merchant_ids)
        
        self.client.post("/api/v1/score", json={
            "transaction_id": f"txn_{random.randint(1, 10**12)}",
            "user_id": user_id,
            "device_id": device_id,
            "merchant_id": merchant_id,
            "amount": round(random.lognormvariate(4, 2), 2),
            "currency": "USD",
            "payment_method": random.choice(["credit_card", "debit_card", "bank_transfer"]),
            "country_code": random.choice(["US", "GB", "SG", "MY", "ID", "TH"]),
            "is_international": random.random() < 0.15,
            "local_hour": random.randint(0, 23)
        })
    
    @task(weight=5)
    def health_check(self):
        self.client.get("/api/v1/health")
```

#### Performance Targets (Pass/Fail Criteria)

| Scenario | Duration | TPS Target | P50 | P95 | P99 | Error Rate | Pass? |
|----------|----------|-----------|-----|-----|-----|-----------|-------|
| **Sustained** | 10 min | ≥ 2,000 | < 50ms | < 100ms | < 200ms | 0% | |
| **Burst (5×)** | 30s | ≥ 5,000 | < 100ms | < 200ms | < 500ms | < 1% | |
| **Ramp** | 5 min | 0 → 3,000 | < 50ms at 2K | — | — | 0% | |
| **Soak** | 1 hour | ≥ 2,000 | < 50ms | < 100ms | < 200ms | 0% | |
| **Degraded (no Redis)** | 5 min | ≥ 500 | < 100ms | < 200ms | < 500ms | 0% | |
| **Streaming latency** | — | — | — | — | — | E2E < 5s | |

### 5.3.2 Security Hardening

| Task | Description | Days |
|------|-------------|------|
| **TLS everywhere** | HTTPS for API Gateway, inter-service TLS (mTLS optional). Redis TLS. ScyllaDB TLS. PostgreSQL SSL. | 2 |
| **Secrets management** | Docker secrets for all credentials. No hardcoded keys. Environment variables for non-sensitive config only. In K8s: Kubernetes Secrets or HashiCorp Vault. | 2 |
| **RBAC** | API key per tenant. JWT with roles: `admin`, `ml-engineer`, `viewer`. Rate limiting per tenant. | 3 |
| **ONNX-only model format** | Enforce ONNX serialization (no pickle). Validate ONNX model integrity with hash on load. | 1 |
| **Dependency scanning** | Add `safety` / `pip-audit` to CI. Block builds with HIGH/CRITICAL CVEs. | 1 |
| **Container scanning** | Trivy or Snyk for Docker image scanning. No root user in containers. Read-only filesystem where possible. | 1 |
| **Input validation** | Pydantic models for all API inputs. Max payload size. Field value ranges. Reject malformed requests at gateway. | 1 |
| **Audit logging** | All model promotions, rollbacks, training triggers logged with user ID, timestamp, reason. | 1 |

**Subtotal: ~12 days**

### 5.3.3 CI/CD Pipeline

| Task | Description | Days |
|------|-------------|------|
| **GitHub Actions / GitLab CI** | Pipeline: lint → test → build images → push to registry → deploy to staging. | 3 |
| **Unit tests** | Python: pytest for scoring service, feature registry, model lifecycle. Target: > 80% coverage on critical paths. | 5 |
| **Integration tests** | Docker Compose test environment. Run: score request → verify response, train model → verify MLflow, CDC event → verify Redis update. | 3 |
| **Feature registry validation** | CI step: `fraudml features validate` — ensures all referenced dbt models and ksqlDB tables exist. | 1 |
| **Image tagging** | Semantic versioning: `fraudml-scoring:1.2.3`. Git SHA tags for traceability. | 0.5 |
| **Staging → Production promotion** | Manual gate (approval in CI) or automated with canary validation. | 1 |
| **Rollback procedure** | Documented: revert to previous image tag. Tested: rollback scoring service within 2 minutes. | 1 |

**Subtotal: ~14.5 days**

#### CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/ci.yml
name: FraudML CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      
      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-dev.txt
      
      - name: Lint (ruff)
        run: ruff check .
      
      - name: Type check (mypy)
        run: mypy serving/ training/ --ignore-missing-imports
      
      - name: Unit tests
        run: pytest tests/unit/ -v --cov=serving --cov=training --cov-report=xml
      
      - name: Feature registry validation
        run: python -m fraudml.features validate
      
      - name: Security scan (pip-audit)
        run: pip-audit --fix --dry-run

  integration-test:
    needs: lint-and-test
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: [6379:6379]
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_DB: fraudml_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports: [5432:5432]
    
    steps:
      - uses: actions/checkout@v4
      - name: Run integration tests
        run: pytest tests/integration/ -v
        env:
          REDIS_URL: redis://localhost:6379
          DATABASE_URL: postgresql://test:test@localhost:5432/fraudml_test

  build-and-push:
    needs: integration-test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build scoring image
        run: docker build -f docker/scoring.Dockerfile -t fraudml-scoring:${{ github.sha }} .
      
      - name: Build training image
        run: docker build -f docker/training.Dockerfile -t fraudml-training:${{ github.sha }} .
      
      - name: Scan images (Trivy)
        run: |
          trivy image --severity HIGH,CRITICAL fraudml-scoring:${{ github.sha }}
          trivy image --severity HIGH,CRITICAL fraudml-training:${{ github.sha }}
      
      - name: Push to registry
        run: |
          docker tag fraudml-scoring:${{ github.sha }} $REGISTRY/fraudml-scoring:${{ github.sha }}
          docker tag fraudml-scoring:${{ github.sha }} $REGISTRY/fraudml-scoring:latest
          docker push $REGISTRY/fraudml-scoring:${{ github.sha }}
          docker push $REGISTRY/fraudml-scoring:latest
```

### 5.3.4 Deployment Automation (Hybrid: On-Prem + Cloud)

| Task | Description | Days |
|------|-------------|------|
| **Docker Compose (on-prem)** | Production-ready compose file with resource limits, restart policies, health checks, logging drivers, volume management. | 2 |
| **Kubernetes Helm charts** | Helm chart for cloud deployment: scoring (Deployment + HPA), training (Job), Redis (StatefulSet), PostgreSQL (StatefulSet), Kafka (Strimzi operator). | 5 |
| **HPA (Horizontal Pod Autoscaler)** | Scale scoring pods based on: CPU > 70%, custom metric (P95 latency > 50ms), or request rate. | 1 |
| **Health probes** | Liveness: `/health` returns 200. Readiness: model loaded + Redis connected + ScyllaDB connected. Startup: model preloading complete. | 1 |
| **Resource quotas** | Per-tenant resource limits (K8s). Prevent one tenant's training from starving another's scoring. | 1 |
| **Backup & disaster recovery** | PostgreSQL: pg_dump daily. ScyllaDB: nodetool snapshot. Redis: RDB + AOF. ClickHouse: backup to S3. Tested restore procedure. | 2 |
| **Deployment documentation** | On-prem: single-page setup guide (Docker Compose). Cloud: Helm chart README + values examples. | 2 |

**Subtotal: ~14 days**

#### Helm Chart Structure

```
helm/fraudml/
├── Chart.yaml
├── values.yaml                 # defaults
├── values-onprem.yaml          # on-prem overrides
├── values-aws.yaml             # AWS overrides
├── values-gcp.yaml             # GCP overrides
├── templates/
│   ├── scoring-deployment.yaml
│   ├── scoring-service.yaml
│   ├── scoring-hpa.yaml
│   ├── training-deployment.yaml
│   ├── redis-statefulset.yaml
│   ├── postgres-statefulset.yaml
│   ├── clickhouse-statefulset.yaml
│   ├── kafka-strimzi.yaml      # Strimzi KafkaCluster CR
│   ├── scylladb-statefulset.yaml
│   ├── ksqldb-deployment.yaml
│   ├── mlflow-deployment.yaml
│   ├── monitoring-cronjob.yaml
│   ├── kong-ingress.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   └── _helpers.tpl
└── tests/
    └── test-scoring.yaml       # Helm test: POST /score → 200
```

```yaml
# values.yaml (defaults)
scoring:
  replicas: 3
  workers: 4
  resources:
    requests:
      cpu: "2"
      memory: "2Gi"
    limits:
      cpu: "4"
      memory: "4Gi"
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPU: 70
    targetLatencyP95Ms: 50

training:
  replicas: 1
  resources:
    requests:
      cpu: "4"
      memory: "8Gi"
    limits:
      cpu: "8"
      memory: "16Gi"
  ray:
    workers: 4
    cpusPerWorker: 4

redis:
  maxmemory: "2gb"
  persistence:
    enabled: true
    size: "10Gi"

postgres:
  storage: "50Gi"
  
clickhouse:
  storage: "200Gi"

scylladb:
  storage: "100Gi"
  replicas: 3

kafka:
  replicas: 3
  storage: "50Gi"
  retention:
    hours: 168    # 7 days

monitoring:
  prometheus:
    retention: "30d"
  grafana:
    enabled: true
  elk:
    enabled: true
```

---

## 5.4 Multi-Tenant Validation

| Task | Description | Days |
|------|-------------|------|
| **Tenant isolation test** | Create 3 tenants. Train different models. Score with each. Verify: no data leakage, no model cross-contamination. | 2 |
| **Per-tenant rate limiting** | Kong rate limiting plugin: 1,000 req/s per tenant API key. Test: exceed limit → 429 returned, other tenants unaffected. | 1 |
| **Per-tenant monitoring** | Grafana dashboard filters by tenant_id. Each tenant sees only their metrics. | 1 |
| **Tenant onboarding guide** | Documented procedure: create tenant → configure DataHub connection → define features → train first model → enable scoring. | 2 |

**Subtotal: ~6 days**

---

## 5.5 Operational Readiness

| Task | Description | Days |
|------|-------------|------|
| **Runbook: scoring service degradation** | Steps: check Grafana → identify bottleneck (Redis/ScyllaDB/CPU) → scale or restart. | 1 |
| **Runbook: training failure** | Steps: check MLflow → inspect logs → retry or escalate. | 0.5 |
| **Runbook: CDC pipeline lag** | Steps: check Kafka consumer lag → check Debezium status → restart connector. | 0.5 |
| **Runbook: model rollback** | Steps: API call → verify scoring reloaded → check Grafana for regression. | 0.5 |
| **Capacity planning calculator** | Spreadsheet/script: input TPS target → output required replicas, Redis memory, ScyllaDB storage, Kafka throughput. | 1 |
| **SLA dashboard** | Grafana: SLA compliance (% time P50 < 50ms, P99 < 200ms, error rate < 0.1%). Monthly trend. | 1 |

**Subtotal: ~4.5 days**

---

## 5.6 Deliverables Checklist

| # | Deliverable | Validation |
|---|------------|------------|
| 1 | Sustained 2K TPS for 10 min | Locust report: 0% errors, P50 < 50ms, P99 < 200ms |
| 2 | Burst 5K TPS for 30s | Locust report: < 1% errors, P99 < 500ms |
| 3 | 1-hour soak test at 2K TPS | No memory leaks, stable latency, 0% errors |
| 4 | Degraded mode (no Redis) at 500 TPS | Locust report: 0% errors, P50 < 100ms |
| 5 | TLS on all connections | `curl --insecure` fails; `curl --cacert` succeeds |
| 6 | No pickle anywhere | Grep codebase: zero `pickle.loads` in serving path |
| 7 | CI/CD pipeline green | Push to main → lint → test → build → scan → push → deploy |
| 8 | Docker Compose production deploy | `docker compose -f docker-compose.prod.yml up` → full stack |
| 9 | Helm chart deploys to K8s | `helm install fraudml ./helm/fraudml` → all pods healthy |
| 10 | Multi-tenant isolation verified | 3 tenants, no data leakage |
| 11 | Runbooks documented | 4 runbooks + capacity planning |
| 12 | SLA dashboard live | 30-day SLA compliance visible |

---

## 5.7 Demo Checkpoint (D5) — Final Demo

### What to Show (Full Platform Demo)

#### Act 1: Platform Setup (5 min)
1. `docker compose up` or `helm install` → full stack starts
2. Show Grafana: all systems green
3. Show Kibana: structured logs flowing

#### Act 2: Data Pipeline (5 min)
4. Show Debezium CDC: source DB → Kafka → ksqlDB → Redis/ScyllaDB
5. Insert a transaction in source DB → show it appear in Redis within 5 seconds
6. Show ksqlDB: real-time velocity aggregates updating

#### Act 3: Training (10 min)
7. Define features: walk through YAML feature registry
8. Build training dataset: ClickHouse → 10M rows → Parquet
9. Multi-model sweep: LightGBM + XGBoost + RF → MLflow comparison
10. Champion auto-selected → promoted to CHAMPION state

#### Act 4: Scoring (5 min)
11. Single score request → full response with features from all 3 sources
12. Locust: ramp to 2,000 TPS → Grafana shows stable performance
13. Show: P50 < 50ms, P99 < 200ms, 0% errors

#### Act 5: ML Lifecycle (10 min)
14. Train new model → STAGING → SHADOW → enable canary (20%)
15. Grafana: champion vs challenger comparison
16. Auto-promote → scoring hot-reloads
17. Rollback → previous champion restored in < 30s

#### Act 6: Monitoring (5 min)
18. Evidently drift report: show PSI per feature
19. Inject drifted data → PSI spikes → alert fires
20. Show SLA dashboard: 30-day compliance

#### Act 7: Resilience (5 min)
21. Kill Redis → scoring continues via ScyllaDB (degraded)
22. Restart Redis → cache repopulates → latency drops back to normal
23. Kill one scoring replica → others absorb load → no client impact

### Total demo time: ~45 minutes

---

## 5.8 Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Performance test results don't meet SLA | Cannot ship | Profile bottlenecks. Common fixes: Redis pipeline batching, larger connection pools, more scoring replicas, enable HTTP/2 in gateway. |
| K8s complexity for on-prem clients | Client can't deploy K8s | Docker Compose is the primary on-prem path. K8s is for cloud/advanced deployments only. |
| Security scan finds critical CVEs | Blocks release | Pin dependencies to patched versions. Use `pip-audit --fix`. Maintain a vulnerability exclusion list for false positives. |
| Soak test reveals memory leak | Scoring degrades over time | Profile with `memray` or `tracemalloc`. Common: unclosed Redis connections, growing TTLCache without eviction, asyncpg pool exhaustion. |
| Multi-tenant resource contention | One tenant's training impacts another's scoring | Kubernetes resource quotas. Docker Compose: CPU pinning (`cpuset`). Training runs on separate nodes from scoring. |
