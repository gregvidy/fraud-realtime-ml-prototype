"""Tests for the KServe InferenceService (B4).

Structural checks on ``kserve/inference_services/fraud_scorer.yaml`` — the
InferenceService that wraps the FastAPI `/score` endpoint unchanged.
"""

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ISVC_PATH = REPO_ROOT / "kserve" / "inference_services" / "fraud_scorer.yaml"


@pytest.fixture(scope="module")
def isvc() -> dict:
    with open(ISVC_PATH) as f:
        return yaml.safe_load(f)


def test_is_valid_kserve_inferenceservice(isvc):
    assert isvc["apiVersion"] == "serving.kserve.io/v1beta1"
    assert isvc["kind"] == "InferenceService"
    assert isvc["metadata"]["name"] == "fraud-scorer"
    assert isvc["metadata"]["namespace"] == "kubeflow"


def test_deployment_mode_is_raw(isvc):
    """RawDeployment overrides the cluster's Serverless default. Change with care —
    Serverless mode has scale-to-zero which breaks warm-up-sensitive latency SLOs."""
    ann = isvc["metadata"].get("annotations", {})
    assert ann.get("serving.kserve.io/deploymentMode") == "RawDeployment"


def test_predictor_has_single_custom_container_named_kserve_container(isvc):
    """The container name MUST be 'kserve-container' — the KServe mutating
    webhook rewrites probes + ports only on that name. Any other name is
    treated as a sidecar."""
    containers = isvc["spec"]["predictor"]["containers"]
    assert len(containers) == 1, "B4 uses a single custom container"
    assert containers[0]["name"] == "kserve-container"


def test_predictor_container_uses_serving_image_and_port_8000(isvc):
    container = isvc["spec"]["predictor"]["containers"][0]
    assert container["image"] == "fraudml/serving:v1"

    ports = container["ports"]
    assert len(ports) == 1
    assert ports[0]["containerPort"] == 8000


def test_readiness_and_liveness_probes_hit_health(isvc):
    """Both probes hit GET /health, which returns 200 once the model is loaded.
    Redis is a soft dep (health returns 'degraded' — still 200 — when down)."""
    container = isvc["spec"]["predictor"]["containers"][0]
    for probe_key in ("readinessProbe", "livenessProbe"):
        probe = container[probe_key]
        assert probe["httpGet"]["path"] == "/health"
        assert probe["httpGet"]["port"] == 8000


def test_env_wires_model_redis_mlflow_feature_defs(isvc):
    container = isvc["spec"]["predictor"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}

    # B5a: MLFLOW_MODEL_URI is the primary source; MODEL_PATH is a fallback
    # that only activates when MLFLOW_MODEL_URI is unset.
    # B5c: registered-model name matches training/experiments/lgbm_v1.yaml's
    # model_registry_name (lgbm_fraud_model).
    assert env["MLFLOW_MODEL_URI"] == "models:/lgbm_fraud_model@production"
    assert env["MODEL_PATH"] == "/app/models/fraud_model.pkl"
    assert env["REDIS_HOST"] == "redis.data-plane.svc.cluster.local"
    assert env["REDIS_PORT"] == "6379"
    assert env["MLFLOW_TRACKING_URI"] == "http://mlflow.data-plane.svc.cluster.local:5000"
    assert env["FRAUDML_FEATURE_DEFS"] == "/app/feature_definitions"


def test_resource_limits_are_bounded(isvc):
    """Prevent a runaway pod from starving the k3d host. Also documents
    a sane starting point for on-prem sizing."""
    container = isvc["spec"]["predictor"]["containers"][0]
    resources = container["resources"]

    # Requests must be set (KServe scheduling relies on them)
    assert "cpu" in resources["requests"]
    assert "memory" in resources["requests"]

    # Limits capped for a local k3d cluster
    assert resources["limits"]["cpu"] in {"1", "2"}
    mem = resources["limits"]["memory"]
    assert mem.endswith("Gi") and int(mem[:-2]) <= 4
