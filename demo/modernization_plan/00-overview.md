# FraudML Modernization Plan — Full Roadmap

## Purpose

This roadmap provides a clear, phased implementation path for building the **FraudML platform** — an embedded, production-grade ML engine within the modernized Predator fraud decision platform. It is designed for engineering leads, architects, and product stakeholders to understand what gets built, when, and why.

---

## Alignment with Predator Modernization

FraudML is the **ML Service** component within the new Predator microservices architecture:

```
Bank Channels
     │
     ▼
┌──────────────┐
│ API Gateway  │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────── Service Layer ────────────────────────────┐
│                                                                               │
│  ┌──────────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  Orchestrator    │  │ ML Service  │  │ Batch        │  │ Comm         │    │
│  │  Service         ├─►│ (FraudML)   │  │ Service      │  │ Service      │    │
│  │  + Rules SDK     │  │             │  │              │  │              │    │
│  └────┬─────────────┘  └──────┬──────┘  └──────────────┘  └──────────────┘    │
│       │                       │                                               │
│       │          ┌────────────┤         Service Registry                      │
│       │          │            │         Management Portal                     │
│       ▼          ▼            ▼         Distributed Messaging                 │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
       │                       │
       ▼                       ▼
┌──────────────────────────────────── Database Layer ───────────────────────────┐
│                                                                               │
│  source db ──► Debezium CDC ──► Kafka ──► ksqlDB ──► sink ──► ScyllaDB        │
│                                                                               │
│  ClickHouse / Snowflake (offline analytics)                                   │
│  Redis (velocity cache)                                                       │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────── Observability ────────────────────────────┐
│  Logstash ──► Elasticsearch ──► Kibana       APM / Prometheus / Grafana       │
└───────────────────────────────────────────────────────────────────────────────┘
```

**FraudML owns**: ML Service, Feature Pipeline (streaming + batch), Model Registry, Monitoring.
**FraudML consumes**: Orchestrator events (via Kafka/messaging), source DB (via CDC), API Gateway routing.
**FraudML produces**: Score responses, model alerts, monitoring metrics to ELK/Grafana.

---

## Phased Roadmap (5 Phases)

The original 8 phases have been consolidated into 5 delivery-focused phases:

| Phase | Name | Merged From | Duration | Goal |
|-------|------|------------|----------|------|
| **1** | Foundation & Core Platform | Phase 0 + 1 | 6-8 weeks | Deployable MVP — containerized, scaled offline, 2K TPS target |
| **2** | Streaming & Durable Storage | Phase 2 + 3 | 6-8 weeks | Production data pipeline — CDC, Kafka, ScyllaDB |
| **3** | Feature Platform & Training at Scale | Phase 4 + 5 | 6-8 weeks | Feature DSL, distributed training, 100M+ rows |
| **4** | ML Lifecycle & Monitoring | Phase 6 + 7 | 4-6 weeks | Champion/challenger, canary, drift detection |
| **5** | Production Hardening & Scale Validation | Phase 8 | 4-6 weeks | Performance SLA, security, deployment automation |

```
Month:    1         2         3         4         5         6         7
          ├─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
Phase 1:  ████████████████████
Phase 2:                      ████████████████████
Phase 3:                                          ████████████████████
Phase 4:                                                    ██████████████
Phase 5:                                                              ████████
Demo:     ──D1──────────D2────────────D3──────────────D4────────────D5──────►
```

Each phase delivers a working, testable increment. Each demo checkpoint (D1–D5) validates the phase deliverables.

---

## Documents in This Folder

| Document | Content |
|----------|---------|
| [01-foundation-core-platform.md](01-foundation-core-platform.md) | Phase 1: Containerization, API gateway, offline storage migration, horizontal scaling |
| [02-streaming-durable-storage.md](02-streaming-durable-storage.md) | Phase 2: Kafka CDC pipeline, ksqlDB streaming features, ScyllaDB durable store |
| [03-feature-platform-training-scale.md](03-feature-platform-training-scale.md) | Phase 3: Feature DSL/registry, Ray distributed training, 100M+ dataset handling |
| [04-ml-lifecycle-monitoring.md](04-ml-lifecycle-monitoring.md) | Phase 4: Champion/challenger, canary deployment, Evidently drift monitoring |
| [05-production-hardening-scale.md](05-production-hardening-scale.md) | Phase 5: 2K TPS validation, security, CI/CD, deployment automation |
| [06-demo-implementation-guide.md](06-demo-implementation-guide.md) | Full demo plan: what to build, how to test, benchmark targets per phase |

---

## Target Metrics

| Metric | Current Prototype | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|--------|-------------------|---------|---------|---------|---------|---------|
| **Scoring TPS** | ~500 (single node) | ≥ 2,000 | ≥ 2,000 | ≥ 2,000 | ≥ 2,000 | ≥ 2,000 (validated) |
| **P50 latency** | ~8ms | < 20ms | < 20ms | < 20ms | < 30ms* | < 50ms |
| **P99 latency** | ~50ms | < 100ms | < 100ms | < 100ms | < 150ms* | < 200ms |
| **Training scale** | ~1M rows | ~10M rows | ~10M rows | **100M+ rows** | 100M+ rows | 100M+ rows |
| **Feature freshness** | Batch (hours) | Batch (hours) | **Real-time (ms)** | Real-time (ms) | Real-time (ms) | Real-time (ms) |
| **Durability** | Redis only | Redis only | **ScyllaDB + Redis** | ScyllaDB + Redis | ScyllaDB + Redis | ScyllaDB + Redis |
| **Model lifecycle** | Manual MLflow | Manual MLflow | Manual MLflow | Manual MLflow | **Full lifecycle** | Full lifecycle |
| **Monitoring** | None | Prometheus/Grafana | Prometheus/Grafana | Prometheus/Grafana | **Evidently drift** | Full observability |

*Phase 4 adds canary shadow-scoring overhead.

---

## Technology Stack Summary

| Layer | Technology | Role | Introduced In |
|-------|-----------|------|---------------|
| **API Gateway** | Kong / NGINX | Routing, rate limiting, auth | Phase 1 |
| **Scoring Service** | FastAPI + Gunicorn + Uvicorn | Real-time fraud scoring | Existing |
| **ML Framework** | LightGBM / XGBoost / ONNX Runtime | Model inference | Existing |
| **Online Store (hot)** | Redis 7 | Velocity features, model cache | Existing |
| **Online Store (durable)** | ScyllaDB | Full feature vectors, fallback | Phase 2 |
| **Offline Store** | ClickHouse (on-prem) / Snowflake (cloud) | Large-scale feature computation | Phase 1 |
| **Feature Transforms** | dbt (ClickHouse adapter) | SQL-based feature engineering | Phase 1 |
| **Streaming** | Kafka + Debezium CDC + ksqlDB | Real-time event pipeline | Phase 2 |
| **Feature Registry** | Custom YAML DSL (Feast-lite) | Feature definitions, versioning | Phase 3 |
| **Distributed Training** | Ray | Parallel training, hyperparameter search | Phase 3 |
| **Experiment Tracking** | MLflow | Model registry, metrics, artifacts | Existing |
| **Drift Monitoring** | Evidently | Feature + prediction drift detection | Phase 4 |
| **Infra Monitoring** | Prometheus + Grafana | Metrics, dashboards, alerting | Phase 1 |
| **Logging** | ELK (Logstash + Elasticsearch + Kibana) | Centralized logging, APM | Phase 1 |
| **Orchestration** | Docker Compose (on-prem) / K8s + Helm (cloud) | Container orchestration | Phase 1 / Phase 5 |
| **Load Testing** | Locust | Performance benchmarking | Existing |

---

## Team & Skills Required

| Role | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|------|---------|---------|---------|---------|---------|
| ML Engineer | 1 | 1 | 2 | 2 | 1 |
| Backend Engineer (Python) | 2 | 2 | 1 | 1 | 1 |
| Data Engineer | 1 | 2 | 2 | 1 | 0 |
| DevOps / Platform | 1 | 1 | 0 | 0 | 2 |
| Frontend (Portal UI) | 0 | 0 | 0 | 1 | 1 |
| **Total** | **5** | **6** | **5** | **5** | **5** |
