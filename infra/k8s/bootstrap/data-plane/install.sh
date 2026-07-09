#!/usr/bin/env bash
# Slice A3a — data plane persistence & tracking installer.
#
# Idempotent. Installs (in order):
#   1. CloudNativePG operator (cnpg-system namespace)
#   2. data-plane namespace
#   3. schema ConfigMap (from sql/bootstrap/*.sql, kept as source of truth)
#   4. Postgres Cluster CR (fraud-db, 1 primary + 1 replica) + credentials
#   5. Redis Deployment (dev, no persistence)
#   6. MLflow Deployment (PG-backed, PVC artifacts)
#   7. MinIO Deployment + bucket-init Job (B5b — training data store)
#
# Waits for readiness after each step so failures surface fast.
#
# Usage: bash infra/k8s/bootstrap/data-plane/install.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../../../.." && pwd)"

log() { printf '[dp-install] %s\n' "$*"; }

log "1/6 install CloudNativePG operator"
kubectl apply --server-side --force-conflicts -f "$DIR/cnpg-operator.yaml" >/dev/null
log "    waiting for cnpg controller Available..."
kubectl -n cnpg-system wait --for=condition=Available deployment/cnpg-controller-manager --timeout=180s

log "2/6 create data-plane namespace"
kubectl apply -f "$DIR/namespace.yaml" >/dev/null

log "3/6 create fraud-db-schema ConfigMap from sql/bootstrap/"
kubectl -n data-plane create configmap fraud-db-schema \
    --from-file="$REPO_ROOT/sql/bootstrap/" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "4/6 apply Postgres Cluster CR + credentials"
kubectl apply -f "$DIR/postgres.yaml" >/dev/null
log "    waiting for fraud-db Cluster to reach Ready phase (up to 5m)..."
ready=false
phase=""
for i in $(seq 1 30); do
    phase=$(kubectl -n data-plane get cluster fraud-db -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [[ "$phase" == "Cluster in healthy state" ]]; then
        log "    fraud-db is healthy"
        ready=true
        break
    fi
    log "    poll #$i phase='${phase}'"
    sleep 10
done
if [[ "$ready" != "true" ]]; then
    log "    ERROR: timed out waiting for fraud-db to become healthy (last phase='${phase}')"
    exit 1
fi

log "5/6 apply Redis Deployment"
kubectl apply -f "$DIR/redis.yaml" >/dev/null
kubectl -n data-plane wait --for=condition=Available deployment/fraud-redis --timeout=120s

log "6/7 apply MLflow Deployment"
kubectl apply -f "$DIR/mlflow.yaml" >/dev/null
log "    waiting for mlflow Available (may take ~90s on first pull)..."
kubectl -n data-plane wait --for=condition=Available deployment/mlflow --timeout=300s

log "7/7 apply MinIO Deployment + bucket-init Job"
kubectl apply -f "$DIR/minio.yaml" >/dev/null
log "    waiting for minio Available..."
kubectl -n data-plane wait --for=condition=Available deployment/minio --timeout=180s
log "    waiting for bucket-init Job to complete..."
kubectl -n data-plane wait --for=condition=Complete job/minio-bucket-init --timeout=120s || {
    log "    WARN: bucket-init Job did not reach Complete within 120s"
}

log "done"
