-- ============================================================================
-- ClickHouse initial bootstrap — schemas
--
-- Runs once, on first container start, as the `default` (admin) user.
-- Users, profiles, quotas, and grants are created by 02-init-rbac.sh
-- (needs shell-side env-var substitution for POC role passwords).
-- ============================================================================

CREATE DATABASE IF NOT EXISTS raw
    COMMENT 'Landing zone: exported operational data from Postgres, streamed events from Redpanda';

CREATE DATABASE IF NOT EXISTS main
    COMMENT 'Analytical models produced by dbt: staging, intermediate, feature tables';

CREATE DATABASE IF NOT EXISTS sandbox
    COMMENT 'Data scientist scratch space for exploratory queries and one-off tables';

