-- =============================================================================
-- Migration: add feature_service_version to model_score_log
-- Run this once on any existing Postgres instance that was bootstrapped
-- before the DuckDB / feature versioning refactor.
-- =============================================================================

ALTER TABLE model_score_log
    ADD COLUMN IF NOT EXISTS feature_service_version VARCHAR(64)
        NOT NULL DEFAULT 'fraud_scoring_v1';
