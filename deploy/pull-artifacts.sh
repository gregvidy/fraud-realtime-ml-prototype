#!/usr/bin/env bash
# ==============================================================================
# pull-artifacts.sh — Download ML artifacts from S3 to the project directory
#
# Called automatically during deploy-push on the EC2 instance.
# Can also be run manually via: make ssm-shell → ./deploy/pull-artifacts.sh
#
# Downloads: parquet feature files, trained model, Feast registry
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

REGION="${REGION:-us-east-1}"

# Detect account ID — on EC2 this uses the instance role
ACCOUNT_ID=$(aws --no-cli-pager sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$ACCOUNT_ID" ]; then
    echo "ERROR: Cannot determine AWS account. Is IAM role attached?"
    exit 1
fi

S3_BUCKET="fraud-demo-deploy-${ACCOUNT_ID}"
S3_PREFIX="s3://$S3_BUCKET/artifacts"

# Detect actual bucket region
BUCKET_REGION=$(aws --no-cli-pager s3api get-bucket-location --bucket "$S3_BUCKET" \
    --query LocationConstraint --output text 2>/dev/null || echo "")
if [ -z "$BUCKET_REGION" ] || [ "$BUCKET_REGION" = "None" ] || [ "$BUCKET_REGION" = "null" ]; then
    BUCKET_REGION="us-east-1"
fi

echo "→ Pulling artifacts from $S3_PREFIX (region: $BUCKET_REGION) ..."

# ── Parquet feature files ─────────────────────────────────────────────────────
PARQUET_DIR="$PROJECT_DIR/data/duckdb/parquet"
mkdir -p "$PARQUET_DIR"
echo "  Parquet files..."
aws s3 sync "$S3_PREFIX/parquet/" "$PARQUET_DIR/" \
    --region "$BUCKET_REGION" --exact-timestamps 2>/dev/null || true

# ── Trained model ─────────────────────────────────────────────────────────────
MODELS_DIR="$PROJECT_DIR/models"
mkdir -p "$MODELS_DIR"
echo "  Model files..."
aws s3 sync "$S3_PREFIX/models/" "$MODELS_DIR/" \
    --region "$BUCKET_REGION" --exact-timestamps 2>/dev/null || true

# ── Feast registry ────────────────────────────────────────────────────────────
FEAST_DATA_DIR="$PROJECT_DIR/feast_repo/feature_repo/data"
mkdir -p "$FEAST_DATA_DIR"
echo "  Feast registry..."
aws s3 cp "$S3_PREFIX/feast/registry.db" "$FEAST_DATA_DIR/registry.db" \
    --region "$BUCKET_REGION" 2>/dev/null || echo "  (no registry in S3 yet)"

# ── DuckDB database (needed for training) ─────────────────────────────────────
DUCKDB_DIR="$PROJECT_DIR/data/duckdb"
mkdir -p "$DUCKDB_DIR"
echo "  DuckDB database..."
aws s3 cp "$S3_PREFIX/duckdb/fraud_offline.duckdb" "$DUCKDB_DIR/fraud_offline.duckdb" \
    --region "$BUCKET_REGION" 2>/dev/null || echo "  (no DuckDB in S3 yet)"

# ── Summary ───────────────────────────────────────────────────────────────────
echo "  Done."
PARQUET_COUNT=$(ls "$PARQUET_DIR"/*.parquet 2>/dev/null | wc -l || echo 0)
MODEL_COUNT=$(ls "$MODELS_DIR"/*.pkl 2>/dev/null | wc -l || echo 0)
echo "  Parquet: $PARQUET_COUNT files | Models: $MODEL_COUNT files"
