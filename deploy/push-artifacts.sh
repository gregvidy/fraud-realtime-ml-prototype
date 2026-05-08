#!/usr/bin/env bash
# ==============================================================================
# push-artifacts.sh — Upload local ML artifacts to S3
#
# Uploads: parquet feature files, trained model, Feast registry
# Run this after: make offline-pipeline && make train
#
# Usage:
#   ./deploy/push-artifacts.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

[ -f "$SCRIPT_DIR/.instance-info" ] && source "$SCRIPT_DIR/.instance-info"
REGION="${REGION:-us-east-1}"

ACCOUNT_ID=$(aws --no-cli-pager sts get-caller-identity --query Account --output text)
S3_BUCKET="fraud-demo-deploy-${ACCOUNT_ID}"
S3_PREFIX="s3://$S3_BUCKET/artifacts"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Pushing ML Artifacts to S3                                 ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Bucket: $S3_BUCKET"
echo "║  Prefix: artifacts/"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Parquet feature files ─────────────────────────────────────────────────────
PARQUET_DIR="$PROJECT_DIR/data/duckdb/parquet"
if [ -d "$PARQUET_DIR" ] && ls "$PARQUET_DIR"/*.parquet &>/dev/null; then
    echo "→ Uploading parquet feature files..."
    aws s3 sync "$PARQUET_DIR" "$S3_PREFIX/parquet/" \
        --region "$REGION" --exclude "*" --include "*.parquet"
    echo "  Done: $(ls "$PARQUET_DIR"/*.parquet | wc -l) files"
else
    echo "⚠ No parquet files found at $PARQUET_DIR"
    echo "  Run: make offline-pipeline"
    exit 1
fi

# ── Trained model ─────────────────────────────────────────────────────────────
MODELS_DIR="$PROJECT_DIR/models"
if ls "$MODELS_DIR"/*.pkl &>/dev/null 2>&1; then
    echo "→ Uploading trained model..."
    aws s3 sync "$MODELS_DIR" "$S3_PREFIX/models/" \
        --region "$REGION" --exclude "*" --include "*.pkl" --include "*.joblib" --include "model_meta.json"
    echo "  Done."
else
    echo "⚠ No model files found at $MODELS_DIR"
    echo "  Run: make train"
    exit 1
fi

# ── Feast registry ────────────────────────────────────────────────────────────
REGISTRY="$PROJECT_DIR/feast_repo/feature_repo/data/registry.db"
if [ -f "$REGISTRY" ]; then
    echo "→ Uploading Feast registry..."
    aws s3 cp "$REGISTRY" "$S3_PREFIX/feast/registry.db" --region "$REGION"
    echo "  Done."
else
    echo "⚠ No Feast registry found. Run: cd feast_repo/feature_repo && feast apply"
fi

# ── DuckDB database (optional — for remote training) ──────────────────────────
DUCKDB="$PROJECT_DIR/data/duckdb/fraud_offline.duckdb"
if [ -f "$DUCKDB" ]; then
    echo "→ Uploading DuckDB database..."
    aws s3 cp "$DUCKDB" "$S3_PREFIX/duckdb/fraud_offline.duckdb" --region "$REGION"
    echo "  Done: $(du -sh "$DUCKDB" | cut -f1)"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Artifacts pushed to S3                                   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next: make deploy-push  (deploys code + pulls artifacts)   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
