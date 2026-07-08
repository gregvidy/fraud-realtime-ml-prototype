#!/usr/bin/env bash
# Slice A2 — Kubeflow standalone install.
#
# Applies the vendored Kubeflow `example` kustomization in the standard retry
# loop (CRDs need to land before CRs; a single-shot apply fails).
#
# Also patches one broken image ref (gcr.io/ml-pipeline/minio was decommissioned
# during Kubeflow's registry migration). Replacement resolves to the same tag on
# Docker Hub. Patch is idempotent and version-scoped to the cache dir.
#
# Idempotent: safe to re-run. Skips clone if cache present.
#
# Usage: bash infra/k8s/bootstrap/kubeflow/install.sh
#        KUBEFLOW_VERSION=v1.10.0 bash infra/k8s/bootstrap/kubeflow/install.sh

set -euo pipefail

KUBEFLOW_VERSION="${KUBEFLOW_VERSION:-v1.10.0}"
CACHE_DIR="${HOME}/.cache/kubeflow-manifests/${KUBEFLOW_VERSION}"
MANIFESTS_REPO="https://github.com/kubeflow/manifests.git"
MAX_APPLY_TRIES=20
APPLY_SLEEP=15

log() { printf '[kubeflow-install] %s\n' "$*"; }

log "target: ${KUBEFLOW_VERSION}"

if [[ ! -d "${CACHE_DIR}/example" ]]; then
    log "cloning manifests → ${CACHE_DIR}"
    mkdir -p "$(dirname "${CACHE_DIR}")"
    git clone --branch "${KUBEFLOW_VERSION}" --depth 1 "${MANIFESTS_REPO}" "${CACHE_DIR}"
else
    log "cache present"
fi

# Patch: rewrite gcr.io/ml-pipeline/minio (decommissioned during Kubeflow's
# registry migration) → docker.io/minio/minio. Same tag; different registry.
KUSTOMIZE_FILE="${CACHE_DIR}/example/kustomization.yaml"
if ! grep -q "# a2-patch-images" "${KUSTOMIZE_FILE}"; then
    log "patching kustomization.yaml: rewrite broken minio image ref"
    cat >> "${KUSTOMIZE_FILE}" <<'EOF'

# a2-patch-images: minio moved off gcr.io after Kubeflow's registry migration.
images:
- name: gcr.io/ml-pipeline/minio
  newName: docker.io/minio/minio
  newTag: RELEASE.2019-08-14T20-37-41Z
EOF
else
    log "kustomization.yaml already patched"
fi

log "current kube context: $(kubectl config current-context)"
log "starting retry-apply loop (CRDs land first, then CRs)"

cd "${CACHE_DIR}"
i=0
until kustomize build example | kubectl apply --server-side --force-conflicts -f - >/dev/null 2>&1 \
   || kustomize build example | kubectl apply -f - >/dev/null 2>&1; do
    i=$((i+1))
    if [[ $i -ge $MAX_APPLY_TRIES ]]; then
        log "ERROR: exceeded ${MAX_APPLY_TRIES} apply attempts"
        exit 1
    fi
    log "apply attempt ${i} not clean, retrying in ${APPLY_SLEEP}s..."
    sleep "${APPLY_SLEEP}"
done

log "apply loop converged after ${i} retries"
log "waiting for Kubeflow deployments to become Available (up to 15m)..."
kubectl -n kubeflow wait --for=condition=Available deployment --all --timeout=900s || {
    log "WARN: some deployments not Available yet. Pods still pulling images most likely."
    kubectl -n kubeflow get pods --field-selector=status.phase!=Running 2>/dev/null || true
}

log "done"
