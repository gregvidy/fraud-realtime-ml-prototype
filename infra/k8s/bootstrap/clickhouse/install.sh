#!/usr/bin/env bash
# Slice A3c — analytical plane installer (Altinity ClickHouse operator).
#
# Idempotent. Installs (in order):
#   1. Altinity clickhouse-operator into kube-system
#   2. fraud-analytics-passwords Secret in data-plane
#   3. ClickHouseInstallation (chi) 'fraud-analytics'
#   4. ch-init-sql ConfigMap (from infra/clickhouse/init.sql + raw_schema.sql)
#   5. ch-schemas-bootstrap Job (creates 3 databases + raw tables)
#   6. ch-rbac-script ConfigMap (from infra/clickhouse/02-init-rbac.sh)
#   7. ch-rbac-bootstrap Job (creates 4 users, profiles, quotas, grants)
#
# Usage: bash infra/k8s/bootstrap/clickhouse/install.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../../../.." && pwd)"

NS=data-plane

log() { printf '[ch-install] %s\n' "$*"; }

log "1/7 install Altinity clickhouse-operator into kube-system"
kubectl apply --server-side --force-conflicts -f "$DIR/operator.yaml" >/dev/null
kubectl -n kube-system wait --for=condition=Available deployment/clickhouse-operator --timeout=180s

log "2/7 ensure ${NS} namespace + passwords Secret"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
kubectl apply -f "$DIR/secrets.yaml" >/dev/null

log "3/7 apply ClickHouseInstallation"
kubectl apply -f "$DIR/chi.yaml" >/dev/null
log "waiting for chi/fraud-analytics status.status=Completed (up to 5m)..."
for i in $(seq 1 30); do
    status=$(kubectl -n "$NS" get chi fraud-analytics -o jsonpath='{.status.status}' 2>/dev/null || echo "")
    if [[ "$status" == "Completed" ]]; then
        log "chi Ready"
        break
    fi
    log "  poll #$i status='${status}'"
    sleep 10
done

log "4/7 build ch-init-sql ConfigMap"
kubectl -n "$NS" create configmap ch-init-sql \
    --from-file="$REPO_ROOT/infra/clickhouse/init.sql" \
    --from-file="$REPO_ROOT/infra/clickhouse/raw_schema.sql" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "5/7 apply schemas Job"
kubectl -n "$NS" delete job ch-schemas-bootstrap --ignore-not-found >/dev/null
kubectl apply -f "$DIR/init-job.yaml" >/dev/null
kubectl -n "$NS" wait --for=condition=Complete job/ch-schemas-bootstrap --timeout=300s || {
    log "schemas Job did not complete, showing logs:"
    kubectl -n "$NS" logs -l job-name=ch-schemas-bootstrap --tail=40
    exit 1
}

log "6/7 build ch-rbac-script ConfigMap"
kubectl -n "$NS" create configmap ch-rbac-script \
    --from-file="$REPO_ROOT/infra/clickhouse/02-init-rbac.sh" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "7/7 apply RBAC Job"
kubectl -n "$NS" delete job ch-rbac-bootstrap --ignore-not-found >/dev/null
kubectl apply -f "$DIR/rbac-job.yaml" >/dev/null
kubectl -n "$NS" wait --for=condition=Complete job/ch-rbac-bootstrap --timeout=180s || {
    log "RBAC Job did not complete, showing logs:"
    kubectl -n "$NS" logs -l job-name=ch-rbac-bootstrap --tail=40
    exit 1
}

log "done"
