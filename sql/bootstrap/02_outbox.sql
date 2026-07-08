-- =============================================================================
-- Bootstrap: Transactional outbox for Predator source integration (Slice 10)
-- Schema: public
-- Run order: 02 (executed automatically on first Postgres start via initdb.d)
--
-- Applied at runtime by scripts/apply_outbox_migration.sh for already-running
-- Postgres containers (this file is idempotent — safe to re-run).
--
-- Design (per demo/modernization_plan_docs/02-streaming-durable-storage.md §2.3.1):
--   Predator services INSERT into raw_transactions AND outbox_events in the
--   same DB transaction. outbox-relay polls unpublished rows with SKIP LOCKED
--   and publishes to Redpanda. Guarantees at-least-once delivery matching DB
--   commits — no dual-write, no lost events on producer crash.
-- =============================================================================

CREATE TABLE IF NOT EXISTS outbox_events (
    id            BIGSERIAL       PRIMARY KEY,
    aggregate_id  TEXT            NOT NULL,   -- e.g. transaction_id
    topic         TEXT            NOT NULL,   -- e.g. txn.raw.visa
    partition_key TEXT            NOT NULL,   -- e.g. user_id (drives Redpanda partitioning)
    avro_value    BYTEA           NOT NULL,   -- Confluent-wire-format Avro payload (magic byte + schema_id + payload)
    schema_id     INT             NOT NULL,   -- Schema Registry subject version (redundant with avro_value[1:5], kept for observability)
    created_at    TIMESTAMPTZ     NOT NULL    DEFAULT NOW(),
    published_at  TIMESTAMPTZ     NULL
);

-- Partial index — the relay's hot path is `WHERE published_at IS NULL`.
-- Skinny index (only unpublished rows) stays tiny even after 10M events.
CREATE INDEX IF NOT EXISTS idx_outbox_unpublished
    ON outbox_events (id)
    WHERE published_at IS NULL;

-- Housekeeping helper: retention for published rows. Run daily via cron/job:
--   DELETE FROM outbox_events WHERE published_at IS NOT NULL AND published_at < NOW() - INTERVAL '7 days';
