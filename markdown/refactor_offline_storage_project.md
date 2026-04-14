# 🚀 Refactor Prompt: Fraud Realtime ML Prototype (Decouple Offline Storage)

You are a senior data platform + ML engineer.

Your task is to **refactor my fraud-realtime-ml-prototype** to decouple
offline feature storage from PostgreSQL and introduce DuckDB as the
offline analytical store.

---

## 🧠 Context

Current stack:

* PostgreSQL → raw data, intermediate tables, feature tables, inference logs
* dbt → ELT pipeline (currently runs fully on Postgres)
* Feast → feature registry + offline/online mapping
* Redis → online feature store
* FastAPI → model inference service

Problems:

* PostgreSQL is overloaded (OLTP + analytics mixed)
* Offline features are hard to scale/manage
* Feature versioning is messy
* Training-serving skew risk is increasing

---

## 🎯 Objective

Refactor architecture into:

* PostgreSQL → operational / transactional only
* DuckDB → offline feature store + training datasets
* dbt → offline transformations (running on DuckDB)
* Redis → online serving features only
* Feast → feature contract layer (offline + online alignment)
* FastAPI → inference

---

## 🧩 High-Level Requirements

1. DO NOT remove PostgreSQL
2. DO NOT move online serving into DuckDB
3. DO NOT duplicate feature logic across systems
4. Introduce clear separation:

   * raw data (Postgres)
   * offline features (DuckDB)
   * online features (Redis)
   * feature definitions (Feast)

---

## 🏗️ Tasks

### 1. Redesign Project Structure

Create a clean modular structure:

/data
/postgres           # raw operational data access
/duckdb             # offline analytical DB + files (parquet/duckdb)

/dbt
/models
/staging
/intermediate
/marts
/features
/training

/feast
/entities
/feature_views
/feature_services

/services
/inference          # FastAPI
/feature_store      # Feast integration
/streaming          # Redis update logic

/scripts
export_pg_to_duckdb.py
materialize_features.py

---

### 2. Introduce DuckDB

Implement:

* Local DuckDB database (file-based)
* Pipeline to export data from PostgreSQL → DuckDB

Options:

* Direct SQL extract → DuckDB
* Export to Parquet → load via DuckDB

Ensure:

* Repeatable snapshot process
* Simple local workflow

---

### 3. Refactor dbt

Change dbt to:

* Use DuckDB as primary target
* Build:

  * staging models (clean raw)
  * intermediate transformations
  * feature tables
  * training datasets

Remove dependency on PostgreSQL for feature marts.

---

### 4. Refactor Feature Pipeline

Implement feature layers:

1. Raw (Postgres)
2. Offline features (DuckDB via dbt)
3. Online features (Redis)

DO NOT:

* recompute features differently in Python
* duplicate SQL logic in multiple places

---

### 5. Integrate Feast Properly

Refactor Feast to:

* Register feature views from DuckDB tables
* Use Redis as online store
* Define feature services per model version

Example:

fraud_model_v1_features:

* card_txn_count_24h_v1
* customer_velocity_1h_v1
* merchant_risk_score_v1

---

### 6. Implement Feature Versioning

Introduce naming convention:

feature_name_window_version

Examples:

* card_txn_count_24h_v1
* customer_txn_velocity_1h_v2

Rules:

* breaking logic change → new version
* no silent overwrites

---

### 7. Fix Training-Serving Skew

Ensure:

* Training uses Feast historical retrieval
* Serving uses Feast online retrieval
* Same feature definitions used everywhere

DO NOT:

* manually recreate features in FastAPI

---

### 8. Update Inference Flow

Flow should be:

1. Transaction → PostgreSQL
2. Update Redis features
3. Fetch features via Feast
4. Run model (FastAPI)
5. Log:

   * features used
   * model version
   * score
   * timestamp

---

### 9. Logging & Reproducibility

Store in PostgreSQL:

* model_version
* feature_service_version
* feature values
* entity_id
* prediction
* timestamp

Goal:
→ full reproducibility of predictions

---

## 📦 Deliverables

1. Updated folder structure
2. DuckDB integration code
3. dbt config for DuckDB
4. Feature view definitions (Feast)
5. Example feature service
6. Example end-to-end flow
7. Migration steps from current system

---

## ⚙️ Constraints

* Must run locally on 16GB MacBook (M3)
* Keep design simple
* Avoid overengineering
* Prefer clarity over scalability

---

## 🧠 Guiding Principle

"dbt builds the data. Feast defines the feature. Redis serves the feature."

---