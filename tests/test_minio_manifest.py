"""Tests for the B5b MinIO manifest — infra/k8s/bootstrap/data-plane/minio.yaml."""

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "infra" / "k8s" / "bootstrap" / "data-plane" / "minio.yaml"


@pytest.fixture(scope="module")
def docs() -> dict:
    with open(MANIFEST) as f:
        raw = list(yaml.safe_load_all(f))
    docs = [d for d in raw if d]
    # Index by (kind, name) for cross-doc assertions
    return {(d["kind"], d["metadata"]["name"]): d for d in docs}


def test_manifest_declares_expected_shapes(docs):
    keys = set(docs.keys())
    assert ("Secret", "minio-root") in keys
    assert ("PersistentVolumeClaim", "minio-data") in keys
    assert ("Deployment", "minio") in keys
    assert ("Service", "minio") in keys
    assert ("Job", "minio-bucket-init") in keys


def test_all_docs_live_in_data_plane_namespace(docs):
    for (kind, name), doc in docs.items():
        assert doc["metadata"]["namespace"] == "data-plane", f"{kind}/{name} in wrong namespace"


def test_secret_carries_root_creds(docs):
    secret = docs[("Secret", "minio-root")]
    assert secret["stringData"]["root-user"] == "minioadmin"
    assert secret["stringData"]["root-password"] == "minioadmin"


def test_pvc_is_bounded_and_rwo(docs):
    pvc = docs[("PersistentVolumeClaim", "minio-data")]
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    storage = pvc["spec"]["resources"]["requests"]["storage"]
    # Sanity envelope — small enough for k3d, large enough for POC parquets
    assert storage.endswith("Gi") and 1 <= int(storage[:-2]) <= 50


def test_deployment_uses_recreate_strategy(docs):
    """PVC is RWO; rolling updates would try to attach the volume to two pods."""
    deploy = docs[("Deployment", "minio")]
    assert deploy["spec"]["strategy"]["type"] == "Recreate"


def test_deployment_wires_secret_env_vars(docs):
    container = docs[("Deployment", "minio")]["spec"]["template"]["spec"]["containers"][0]
    env_names = {e["name"]: e for e in container["env"]}
    for var in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        assert var in env_names
        assert env_names[var]["valueFrom"]["secretKeyRef"]["name"] == "minio-root"


def test_deployment_exposes_s3_and_console_ports(docs):
    ports = docs[("Deployment", "minio")]["spec"]["template"]["spec"]["containers"][0]["ports"]
    port_map = {p["name"]: p["containerPort"] for p in ports}
    assert port_map == {"s3": 9000, "console": 9001}


def test_deployment_has_health_probes(docs):
    container = docs[("Deployment", "minio")]["spec"]["template"]["spec"]["containers"][0]
    for probe_key, path in (("readinessProbe", "/minio/health/ready"),
                            ("livenessProbe", "/minio/health/live")):
        probe = container[probe_key]
        assert probe["httpGet"]["path"] == path
        assert probe["httpGet"]["port"] == 9000


def test_service_maps_both_ports(docs):
    svc = docs[("Service", "minio")]
    ports = {p["name"]: p["port"] for p in svc["spec"]["ports"]}
    assert ports == {"s3": 9000, "console": 9001}


def test_bucket_init_job_creates_fraudml_data_bucket(docs):
    job = docs[("Job", "minio-bucket-init")]
    container = job["spec"]["template"]["spec"]["containers"][0]
    args = "\n".join(container["args"])
    assert "mc mb --ignore-existing fraud/fraudml-data" in args
    # Job must resolve via the in-cluster Service DNS, not localhost
    assert "http://minio.data-plane.svc.cluster.local:9000" in args


def test_bucket_init_job_uses_root_secret(docs):
    container = docs[("Job", "minio-bucket-init")]["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}
    for var in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        assert env[var]["valueFrom"]["secretKeyRef"]["name"] == "minio-root"
