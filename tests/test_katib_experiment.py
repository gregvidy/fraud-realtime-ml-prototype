"""Tests for the Katib LightGBM HPO Experiment (B3).

Two layers:
  1. Structural checks on ``katib/experiments/lgbm_hpo.yaml`` — algorithm,
     parameters, image, metrics collector regex.
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


# ---------------------------------------------------------------------------
# 1. Katib Experiment YAML structural tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def experiment() -> dict:
    with open(EXPERIMENT_PATH) as f:
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
