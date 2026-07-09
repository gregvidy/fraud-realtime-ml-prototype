#!/usr/bin/env bash
# Slice A3b — streaming plane installer.
#
# Idempotent. Installs (in order):
#   1. helm repo redpanda (idempotent)
#   2. redpanda/redpanda chart into data-plane ns (1 broker + SR + Console)
#   3. wait for redpanda-0 pod Ready
#   4. ConfigMap `redpanda-schemas` from streaming/schemas/*.avsc
#   5. Job `redpanda-topics-bootstrap` (creates 8 topics)
#   6. Job `redpanda-schemas-bootstrap` (registers 8 Avro subjects)
#
# Usage: bash infra/k8s/bootstrap/streaming/install.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../../../.." && pwd)"

CHART_VERSION="${CHART_VERSION:-26.1.8}"
NS=data-plane

log() { printf '[stream-install] %s\n' "$*"; }

log "1/6 ensure redpanda helm repo"
helm repo add redpanda https://charts.redpanda.com >/dev/null 2>&1 || true
helm repo update redpanda >/dev/null

log "2/6 install redpanda chart v${CHART_VERSION} into ${NS} ns"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
helm upgrade --install redpanda redpanda/redpanda \
    --version "$CHART_VERSION" \
    --namespace "$NS" \
    -f "$DIR/values.yaml" \
    --wait --timeout 10m >/dev/null

log "3/6 wait for redpanda-0 pod Ready"
kubectl -n "$NS" wait --for=condition=Ready pod/redpanda-0 --timeout=300s

log "4/6 build redpanda-schemas ConfigMap from streaming/schemas/*.avsc"
kubectl -n "$NS" create configmap redpanda-schemas \
    --from-file="$REPO_ROOT/streaming/schemas/" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

log "5/6 apply topics-bootstrap Job"
kubectl -n "$NS" delete job redpanda-topics-bootstrap --ignore-not-found >/dev/null
kubectl apply -f "$DIR/topics-job.yaml" >/dev/null
kubectl -n "$NS" wait --for=condition=Complete job/redpanda-topics-bootstrap --timeout=180s || {
    log "topics Job did not complete, showing logs:"
    kubectl -n "$NS" logs -l job-name=redpanda-topics-bootstrap --tail=40
    exit 1
}

log "6/6 apply schemas-bootstrap Job"
kubectl -n "$NS" delete job redpanda-schemas-bootstrap --ignore-not-found >/dev/null
kubectl apply -f "$DIR/schemas-job.yaml" >/dev/null
kubectl -n "$NS" wait --for=condition=Complete job/redpanda-schemas-bootstrap --timeout=180s || {
    log "schemas Job did not complete, showing logs:"
    kubectl -n "$NS" logs -l job-name=redpanda-schemas-bootstrap --tail=40
    exit 1
}

log "done"
