"""Tests for the KFP training pipeline (B2)."""

from pathlib import Path

import yaml

from fraudml.pipelines.training_pipeline import training_pipeline
from kfp.compiler import Compiler


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_compiles(tmp_path):
    out = tmp_path / "pipeline.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(out))
    assert out.exists() and out.stat().st_size > 0


def test_compiled_yaml_has_three_components(tmp_path):
    out = tmp_path / "pipeline.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(out))
    with open(out) as f:
        ir = yaml.safe_load(f)

    comps = ir["components"]
    expected = {"comp-build-dataset", "comp-train", "comp-evaluate"}
    assert set(comps.keys()) == expected


def test_dag_ordering_is_linear(tmp_path):
    """build_dataset → train → evaluate. train depends on build; evaluate on train."""
    out = tmp_path / "pipeline.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(out))
    with open(out) as f:
        ir = yaml.safe_load(f)

    dag = ir["root"]["dag"]["tasks"]

    train_deps = dag["train"].get("dependentTasks", [])
    eval_deps = dag["evaluate"].get("dependentTasks", [])

    assert "build-dataset" in train_deps, f"train must depend on build-dataset, got {train_deps}"
    assert "train" in eval_deps, f"evaluate must depend on train, got {eval_deps}"


def test_all_components_use_training_image(tmp_path):
    out = tmp_path / "pipeline.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(out))
    with open(out) as f:
        ir = yaml.safe_load(f)

    executors = ir["deploymentSpec"]["executors"]
    for name, exec_spec in executors.items():
        img = exec_spec["container"]["image"]
        assert img == "fraudml/training:v1", f"{name}: unexpected image {img!r}"


def test_env_vars_set_on_every_component(tmp_path):
    out = tmp_path / "pipeline.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(out))
    with open(out) as f:
        ir = yaml.safe_load(f)

    executors = ir["deploymentSpec"]["executors"]
    for name, exec_spec in executors.items():
        env = {e["name"]: e["value"] for e in exec_spec["container"].get("env", [])}
        assert "MLFLOW_TRACKING_URI" in env, f"{name}: missing MLFLOW_TRACKING_URI"
        assert "FRAUDML_FEATURE_DEFS" in env, f"{name}: missing FRAUDML_FEATURE_DEFS"
        # B5c: MinIO wiring so KFP components can read training data from S3.
        assert "TRAINING_DATA_URI" in env, f"{name}: missing TRAINING_DATA_URI"
        assert env["TRAINING_DATA_URI"].startswith("s3://"), f"{name}: TRAINING_DATA_URI must be an S3 URI"
        for aws_var in ("AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
            assert aws_var in env, f"{name}: missing {aws_var}"


def test_committed_yaml_matches_current_source(tmp_path):
    """The checked-in fraudml/pipelines/training_pipeline.yaml MUST match a fresh compile.

    Prevents the committed YAML from drifting out of sync with the Python source.
    Run `make pipeline-compile` after any change to training_pipeline.py.
    """
    fresh = tmp_path / "fresh.yaml"
    Compiler().compile(pipeline_func=training_pipeline, package_path=str(fresh))

    committed = REPO_ROOT / "fraudml" / "pipelines" / "training_pipeline.yaml"
    assert committed.exists(), "committed pipeline YAML missing — run `make pipeline-compile`"

    assert fresh.read_text() == committed.read_text(), (
        "committed pipeline YAML is stale — run `make pipeline-compile` and commit"
    )
