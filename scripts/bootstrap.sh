#!/usr/bin/env bash
# End-to-end platform bootstrap (B5c).
#
# Idempotent chain that provisions every layer from an empty machine to a live
# InferenceService. Assumes:
#   * conda env 'fraud-realtime-ml' is activated (or MAKE will use CONDA_PREFIX)
#   * local ClickHouse + Postgres are running (docker-compose up -d …) so
#     `make offline-pipeline && make train` can produce local parquets first
#   * kubectl points at the k3d cluster fraud-platform
#
# Each step wraps a make target that's already independently tested. Failures
# surface immediately (set -e). Reruns are safe — everything downstream of a
# fresh cluster is idempotent.
set -euo pipefail

log() { printf '\n[bootstrap] %s\n' "$*"; }

step=1
total=9

log "$step/$total  cluster-up (k3d)"; step=$((step + 1))
make cluster-up

log "$step/$total  data-plane (Postgres + Redis + MLflow + MinIO)"; step=$((step + 1))
make dp-up

log "$step/$total  Kubeflow (KFP + Katib + KServe)"; step=$((step + 1))
make kubeflow-up

log "$step/$total  upload local parquets → MinIO (temporary port-forward)"; step=$((step + 1))
kubectl -n data-plane port-forward svc/minio 9000:9000 >/tmp/bootstrap-pf.log 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 3
make bootstrap-data
kill $PF_PID 2>/dev/null || true
trap - EXIT

log "$step/$total  train + register model in MLflow"; step=$((step + 1))
# Training on the HOST reads local data/parquet — no MinIO needed at this step.
# The resulting mlruns/ are picked up by the MLflow container via PVC.
make train

log "$step/$total  promote-latest → alias 'production'"; step=$((step + 1))
make promote-latest REGISTERED_MODEL=lgbm_fraud_model ALIAS=production

log "$step/$total  build + import serving image"; step=$((step + 1))
make serve-image-build
make serve-image-import

log "$step/$total  build + import training image"; step=$((step + 1))
make pipeline-image-build
make pipeline-image-import

log "$step/$total  deploy InferenceService"; step=$((step + 1))
make kserve-apply

log "done — cluster is fully wired. Try 'kubectl -n kubeflow get inferenceservice fraud-scorer'"
