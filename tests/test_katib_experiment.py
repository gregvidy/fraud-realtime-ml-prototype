"""Tests for the Katib HPO Experiments (B3).

Two layers:
  1. Structural checks on the Experiment YAMLs — algorithm, parameters,
     image, metrics collector regex. Covers both LightGBM and XGBoost.
  2. Unit tests for ``training.katib_trial.apply_overrides`` — the wrapper
     that Katib invokes for each trial.
"""

import re
from pathlib import Path

import pytest
import yaml

from training.katib_trial import apply_overrides


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = REPO_ROOT / "katib" / "experiments" / "lgbm_hpo.yaml"
XGBOOST_EXPERIMENT_PATH = REPO_ROOT / "katib" / "experiments" / "xgboost_hpo.yaml"


# ---------------------------------------------------------------------------
# 1. Katib Experiment YAML structural tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def experiment() -> dict:
    with open(EXPERIMENT_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def xgboost_experiment() -> dict:
    with open(XGBOOST_EXPERIMENT_PATH) as f:
        return yaml.safe_load(f)


def test_experiment_is_valid_kubeflow_experiment(experiment):
    assert experiment["apiVersion"] == "kubeflow.org/v1beta1"
    assert experiment["kind"] == "Experiment"
    assert experiment["metadata"]["namespace"] == "kubeflow"
    assert experiment["metadata"]["name"] == "fraudml-lgbm-hpo"


def test_objective_is_maximise_pr_auc(experiment):
    obj = experiment["spec"]["objective"]
    assert obj["type"] == "maximize"
    assert obj["objectiveMetricName"] == "PR-AUC"
    assert "ROC-AUC" in obj["additionalMetricNames"]


def test_algorithm_is_random(experiment):
    assert experiment["spec"]["algorithm"]["algorithmName"] == "random"


def test_parameter_space_covers_four_lgbm_hps(experiment):
    params = experiment["spec"]["parameters"]
    names = {p["name"] for p in params}
    assert names == {"num-leaves", "learning-rate", "n-estimators", "subsample"}


def test_trial_budget_is_bounded(experiment):
    spec = experiment["spec"]
    # Must have a hard cap so a broken image can't chew resources indefinitely.
    assert spec["maxTrialCount"] <= 20
    assert spec["parallelTrialCount"] <= spec["maxTrialCount"]
    assert spec["maxFailedTrialCount"] >= 1


def test_trial_template_uses_training_image(experiment):
    tmpl = experiment["spec"]["trialTemplate"]["trialSpec"]
    container = tmpl["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "fraudml/training:v1"
    assert container["name"] == "training"


def test_trial_template_invokes_katib_trial_wrapper(experiment):
    tmpl = experiment["spec"]["trialTemplate"]["trialSpec"]
    container = tmpl["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "training.katib_trial"]

    args = container["args"]
    # Base config passed via --config
    assert "--config" in args
    assert "training/experiments/lgbm_v1.yaml" in args

    # Each of the 4 HPs is injected via --set with a trialParameters placeholder
    set_pairs = [args[i + 1] for i, a in enumerate(args) if a == "--set"]
    joined = "\n".join(set_pairs)
    for placeholder in [
        "${trialParameters.numLeaves}",
        "${trialParameters.learningRate}",
        "${trialParameters.nEstimators}",
        "${trialParameters.subsample}",
    ]:
        assert placeholder in joined


def test_env_vars_wire_mlflow_and_feature_registry(experiment):
    container = (
        experiment["spec"]["trialTemplate"]["trialSpec"]
        ["spec"]["template"]["spec"]["containers"][0]
    )
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["MLFLOW_TRACKING_URI"] == "http://mlflow.data-plane.svc.cluster.local:5000"
    assert env["FRAUDML_FEATURE_DEFS"] == "/app/feature_definitions"
    # B5c: trials fetch training data from MinIO.
    assert env["TRAINING_DATA_URI"].startswith("s3://")
    assert env["AWS_ENDPOINT_URL_S3"] == "http://minio.data-plane.svc.cluster.local:9000"
    for aws_var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        assert aws_var in env, f"missing {aws_var}"


def test_stdout_regex_matches_actual_training_output(experiment):
    """The metricsFormat regex MUST match lines train_model.py actually prints."""
    regexes = experiment["spec"]["metricsCollectorSpec"]["source"]["filter"]["metricsFormat"]

    # Actual line printed by training/train_model.py (line 553):
    #   f"{split_label} metrics → ROC-AUC={roc_auc:.4f}  PR-AUC={pr_auc:.4f}"
    sample = "OOT metrics → ROC-AUC=0.8712  PR-AUC=0.4732"

    matched = {}
    for pattern in regexes:
        for m in re.finditer(pattern, sample):
            matched[m.group(1)] = float(m.group(2))

    assert matched == {"ROC-AUC": 0.8712, "PR-AUC": 0.4732}


def test_trial_parameter_references_match_search_space(experiment):
    """Every ``trialParameters.reference`` must exist in ``spec.parameters``."""
    search_space = {p["name"] for p in experiment["spec"]["parameters"]}
    refs = {tp["reference"] for tp in experiment["spec"]["trialTemplate"]["trialParameters"]}
    assert refs.issubset(search_space), f"orphan trialParameter refs: {refs - search_space}"


# ---------------------------------------------------------------------------
# 1b. XGBoost Experiment structural tests
# ---------------------------------------------------------------------------

def test_xgboost_experiment_is_valid_kubeflow_experiment(xgboost_experiment):
    assert xgboost_experiment["apiVersion"] == "kubeflow.org/v1beta1"
    assert xgboost_experiment["kind"] == "Experiment"
    assert xgboost_experiment["metadata"]["namespace"] == "kubeflow"
    assert xgboost_experiment["metadata"]["name"] == "fraudml-xgboost-hpo"


def test_xgboost_shares_objective_and_algorithm_with_lgbm(experiment, xgboost_experiment):
    """Both HPO experiments optimise the same metric with the same algorithm.
    If we later switch LGBM to TPE, XGBoost should be updated in the same PR."""
    l_obj = experiment["spec"]["objective"]
    x_obj = xgboost_experiment["spec"]["objective"]
    assert (l_obj["type"], l_obj["objectiveMetricName"]) == (
        x_obj["type"], x_obj["objectiveMetricName"],
    )
    assert experiment["spec"]["algorithm"] == xgboost_experiment["spec"]["algorithm"]


def test_xgboost_parameter_space_covers_five_hps(xgboost_experiment):
    params = xgboost_experiment["spec"]["parameters"]
    names = {p["name"] for p in params}
    assert names == {"max-depth", "learning-rate", "n-estimators", "subsample", "colsample-bytree"}


def test_xgboost_trial_template_uses_training_image(xgboost_experiment):
    container = (
        xgboost_experiment["spec"]["trialTemplate"]["trialSpec"]
        ["spec"]["template"]["spec"]["containers"][0]
    )
    assert container["image"] == "fraudml/training:v1"
    assert container["command"] == ["python", "-m", "training.katib_trial"]


def test_xgboost_trial_pins_model_type_and_injects_five_hps(xgboost_experiment):
    """XGBoost trials must override `model.type=xgboost` (constant) AND inject
    all 5 trial-parameter placeholders into `model.xgboost.*`."""
    container = (
        xgboost_experiment["spec"]["trialTemplate"]["trialSpec"]
        ["spec"]["template"]["spec"]["containers"][0]
    )
    args = container["args"]
    set_pairs = [args[i + 1] for i, a in enumerate(args) if a == "--set"]
    joined = "\n".join(set_pairs)

    # Constant override — pins the model family so train_model.py dispatches to xgboost
    assert "model.type=xgboost" in joined

    # Every trialParameter placeholder must appear in --set args, targeting model.xgboost.*
    for placeholder, cfg_key in [
        ("${trialParameters.maxDepth}", "model.xgboost.max_depth"),
        ("${trialParameters.learningRate}", "model.xgboost.learning_rate"),
        ("${trialParameters.nEstimators}", "model.xgboost.n_estimators"),
        ("${trialParameters.subsample}", "model.xgboost.subsample"),
        ("${trialParameters.colsampleBytree}", "model.xgboost.colsample_bytree"),
    ]:
        assert f"{cfg_key}={placeholder}" in set_pairs, (
            f"missing --set {cfg_key}={placeholder}"
        )


def test_xgboost_trial_parameter_references_match_search_space(xgboost_experiment):
    search_space = {p["name"] for p in xgboost_experiment["spec"]["parameters"]}
    refs = {tp["reference"] for tp in xgboost_experiment["spec"]["trialTemplate"]["trialParameters"]}
    assert refs.issubset(search_space), f"orphan trialParameter refs: {refs - search_space}"


def test_xgboost_env_vars_wire_mlflow_and_feature_registry(xgboost_experiment):
    container = (
        xgboost_experiment["spec"]["trialTemplate"]["trialSpec"]
        ["spec"]["template"]["spec"]["containers"][0]
    )
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["MLFLOW_TRACKING_URI"] == "http://mlflow.data-plane.svc.cluster.local:5000"
    assert env["FRAUDML_FEATURE_DEFS"] == "/app/feature_definitions"
    # Distinct MLflow experiment name so LGBM and XGBoost runs don't collide
    assert env["MLFLOW_EXPERIMENT_NAME"] == "fraudml-xgboost-hpo"
    # B5c: trials fetch training data from MinIO (same wiring as LGBM).
    assert env["TRAINING_DATA_URI"].startswith("s3://")
    assert env["AWS_ENDPOINT_URL_S3"] == "http://minio.data-plane.svc.cluster.local:9000"


# ---------------------------------------------------------------------------
# 2. Trial-wrapper unit tests
# ---------------------------------------------------------------------------

def _base_config() -> dict:
    return {
        "data": {"path": "training/datasets/training_dataset.parquet"},
        "model": {
            "type": "lightgbm",
            "lightgbm": {
                "num_leaves": 63,
                "learning_rate": 0.05,
                "n_estimators": 500,
                "subsample": 0.8,
            },
        },
    }


def test_apply_overrides_replaces_scalar_and_coerces_types():
    out = apply_overrides(
        _base_config(),
        [
            "model.lightgbm.num_leaves=31",
            "model.lightgbm.learning_rate=0.07",
            "model.lightgbm.n_estimators=250",
            "model.lightgbm.subsample=0.9",
        ],
    )
    lgbm = out["model"]["lightgbm"]
    assert lgbm["num_leaves"] == 31 and isinstance(lgbm["num_leaves"], int)
    assert lgbm["learning_rate"] == 0.07 and isinstance(lgbm["learning_rate"], float)
    assert lgbm["n_estimators"] == 250
    assert lgbm["subsample"] == 0.9


def test_apply_overrides_does_not_mutate_input():
    base = _base_config()
    apply_overrides(base, ["model.lightgbm.num_leaves=31"])
    assert base["model"]["lightgbm"]["num_leaves"] == 63  # untouched


def test_apply_overrides_rejects_missing_equals():
    with pytest.raises(ValueError, match="dotted.key=value"):
        apply_overrides(_base_config(), ["model.lightgbm.num_leaves"])


def test_apply_overrides_rejects_unknown_leaf_key():
    with pytest.raises(ValueError, match="not found"):
        apply_overrides(_base_config(), ["model.lightgbm.does_not_exist=1"])


def test_apply_overrides_rejects_unknown_path_segment():
    with pytest.raises(ValueError, match="not found"):
        apply_overrides(_base_config(), ["model.xgboost.max_depth=6"])


def test_apply_overrides_switches_model_type_and_keeps_string_scalar():
    """XGBoost HPO trials override `model.type=xgboost` (string) alongside
    numeric --set overrides. Verify the string stays a string (yaml.safe_load
    would happily turn `1` into int(1); `xgboost` must stay as str)."""
    base = {
        "model": {
            "type": "lightgbm",
            "xgboost": {"max_depth": 6, "learning_rate": 0.05},
        },
    }
    out = apply_overrides(
        base,
        [
            "model.type=xgboost",
            "model.xgboost.max_depth=8",
            "model.xgboost.learning_rate=0.07",
        ],
    )
    assert out["model"]["type"] == "xgboost"
    assert isinstance(out["model"]["type"], str)
    assert out["model"]["xgboost"]["max_depth"] == 8
    assert isinstance(out["model"]["xgboost"]["max_depth"], int)
    assert out["model"]["xgboost"]["learning_rate"] == 0.07
    assert isinstance(out["model"]["xgboost"]["learning_rate"], float)
