## What Went Well

**Architecture design is sound**

The two-tier feature separation is correct — slow batch features (DuckDB → Redis via Feast) and fast real-time features (Redis Sorted Sets) serve different latency requirements. This mirrors what production systems like Stripe and PayPal actually use.

**Training-serving consistency is addressed**

`fct_training_dataset` in dbt computes the same feature logic used at serving time. `online_feature_log` in Postgres captures actual inference-time values for retraining. This is the single hardest problem in production ML and the project has a real solution for it, not just a placeholder.

**Config-driven training pipeline**

`training_config.yaml` driving model type, split strategy, preprocessing, calibration — this is production-minded. Changing experiments without code changes is important for iteration speed.

**Calibration is included**

Most fraud ML projects skip this. Having isotonic/sigmoid/beta calibration in the pipeline means the model's probability outputs are actually meaningful for risk banding, not just ranking.

**Graceful degradation**

Feast/Redis failures fall back to defaults instead of crashing. This is critical for availability in production.

---

## What Would Need to Change for Enterprise Production

**1. The event bus is missing — this is the biggest gap**

```
Current:  stream_transactions.py → updater.py (direct in-process call)
Required: Transactions → Kafka topic → consumer updates Redis
```

stream_transactions.py calls updater.py directly in the same process. In production, new transactions arrive from dozens of upstream services (mobile app, web, POS terminals). They need to publish to a **Kafka topic**, and a separate consumer service updates Redis. Without this, there's no horizontal scaling and no event replay.

**2. DuckDB is not production-safe for the offline store**

DuckDB is a single-file embedded database with no concurrent write support. In production this becomes:
- **Postgres** with columnar extensions (Timescale), or
- **BigQuery / Snowflake / Redshift** as the offline store

dbt stays the same — you just change the profile target.

**3. No model registry or deployment pipeline**

```
Current:  models/*.pkl on local disk, loaded at startup
Required: MLflow / SageMaker Model Registry → versioned artifacts
          → blue/green or canary deployment
```

Right now, `make train` overwrites `models/fraud_model.pkl` and a restart picks it up. In production you need version control, rollback capability, and shadow scoring (run new model in parallel before cutover).

**4. Single-entity Feast lookup is inefficient**

```python
# Current — one Redis round trip per entity type
store.get_online_features(entity_rows=[{"user_id": ..., "device_id": ..., "merchant_id": ...}])
```

Feast's local file offline store + single-row entity lookup adds ~10-30ms overhead. At enterprise scale (thousands of TPS) you'd bypass Feast's SDK entirely and do **direct Redis pipelining** — one batched round trip for all three entity keys simultaneously.

**5. No champion/challenger or A/B framework**

There's a `FEATURE_SERVICE_VERSION` constant in scoring.py but no routing logic. Production fraud systems always run at least two models simultaneously — new model on 5% of traffic, compare KPIs before full rollout.

**6. Feedback loop latency**

Fraud labels (`is_fraud`) come from chargebacks which arrive 30-90 days after the transaction. The current pipeline has no mechanism for:
- Delayed label ingestion
- Re-scoring historical transactions with updated labels
- Concept drift detection (fraud patterns shift, model degrades silently)

**7. No feature store TTL strategy for cold entities**

New users/devices have no history in Redis. The current code returns 0s for all features — which is actually correct behaviour but it's not documented or monitored. In production, cold-start handling (e.g. population averages as defaults, or a separate new-account risk model) is a significant product decision.

---

## What's Genuinely Overengineered for a Prototype

- The Feast registry layer for a single-model, single-team setup adds operational overhead without proportional benefit
- `beta` calibration is very rarely needed; isotonic covers 99% of cases
- `online_feature_log` in Postgres is a good idea but will become a write bottleneck under high TPS — production would use Kafka → S3/GCS as the log sink

---

## Summary

```
Component                  Prototype grade    Production gap
─────────────────────────────────────────────────────────────
Feature architecture       A                 Kafka needed
Training pipeline          A-                MLflow/registry needed
Serving API                B+                Direct Redis pipelining
Offline store (DuckDB)     B  (ok for dev)   Replace with warehouse
Online store (Redis)       A-                Fine, needs TTL design
Event streaming            C                 In-process only
Model lifecycle            C                 No versioning/rollback
Feedback/retraining loop   C+                No drift detection
```

The core ML logic and architecture decisions are solid — it's the **infrastructure connective tissue** (Kafka, model registry, deployment pipeline, drift monitoring) that separates this from production-ready. That's normal and expected for a prototype; those pieces are operationally heavy and don't belong in an MVP.