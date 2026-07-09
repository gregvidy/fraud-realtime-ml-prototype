"""Tests for the B5a MLflow-registry branch of app.model_loader.

Two layers:
  1. Pure-Python unit tests for ``_parse_models_uri`` (URI parsing).
  2. End-to-end unit test that ``load_model()`` honours ``MLFLOW_MODEL_URI``
     by staging artifacts through a mocked MLflow client. No live MLflow
     server is needed.

The MODEL_PATH (joblib-only) path is exercised elsewhere; here we only
verify the new MLflow branch and that MLFLOW_MODEL_URI takes precedence.
"""

import importlib
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import joblib
import pytest
from sklearn.linear_model import LogisticRegression

import app.model_loader as loader


# ---------------------------------------------------------------------------
# URI parser
# ---------------------------------------------------------------------------

def test_parse_models_uri_alias_form():
    name, ref, is_alias = loader._parse_models_uri("models:/fraud_model@production")
    assert name == "fraud_model"
    assert ref == "production"
    assert is_alias is True


def test_parse_models_uri_version_form():
    name, ref, is_alias = loader._parse_models_uri("models:/fraud_model/7")
    assert name == "fraud_model"
    assert ref == "7"
    assert is_alias is False


def test_parse_models_uri_rejects_bad_scheme():
    with pytest.raises(ValueError, match="models:/"):
        loader._parse_models_uri("s3://bucket/fraud")


def test_parse_models_uri_rejects_bare_name():
    with pytest.raises(ValueError, match="alias"):
        loader._parse_models_uri("models:/fraud_model")


# ---------------------------------------------------------------------------
# End-to-end: MLFLOW_MODEL_URI triggers the MLflow staging path
# ---------------------------------------------------------------------------

@pytest.fixture
def _reset_loader():
    """Clear the module-level singletons around each test so load_model()
    executes end-to-end instead of returning the cached instance."""
    loader._model = None
    loader._prep = None
    loader._meta = {}
    loader._calib_x = None
    loader._calib_y = None
    yield
    loader._model = None
    loader._prep = None
    loader._meta = {}
    loader._calib_x = None
    loader._calib_y = None


def _write_fake_run(root: Path, model_name: str = "fraud_model") -> Path:
    """Build a directory that mirrors what mlflow.artifacts.download_artifacts
    yields for a run created by training/train_model.py — artifacts/ (pkl)
    + config/ (meta). Returns the root; the loader must find the base pkl."""
    (root / "artifacts").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    model_pkl = root / "artifacts" / f"{model_name}.pkl"
    joblib.dump(LogisticRegression(), model_pkl)

    meta = {
        "model_name": model_name,
        "threshold": 0.42,
        "feature_cols": ["txn_amount", "user_age_days"],
    }
    (root / "config" / "model_meta.json").write_text(json.dumps(meta))
    return root


def test_load_model_from_mlflow_uri_stages_and_loads(monkeypatch, tmp_path, _reset_loader):
    fake_run_dir = _write_fake_run(tmp_path / "run")

    fake_mv = MagicMock()
    fake_mv.run_id = "abcdef1234"
    fake_mv.version = "3"

    fake_client = MagicMock()
    fake_client.get_model_version_by_alias.return_value = fake_mv

    with patch("mlflow.MlflowClient", return_value=fake_client), \
         patch("mlflow.artifacts.download_artifacts", return_value=str(fake_run_dir)):
        monkeypatch.setenv("MLFLOW_MODEL_URI", "models:/fraud_model@production")
        monkeypatch.delenv("MODEL_PATH", raising=False)

        model, meta = loader.load_model()

    fake_client.get_model_version_by_alias.assert_called_once_with(
        "fraud_model", "production",
    )
    assert isinstance(model, LogisticRegression)
    assert meta["threshold"] == 0.42
    assert meta["feature_cols"] == ["txn_amount", "user_age_days"]

    # meta must have been copied into artifacts/ next to the pkl
    assert (fake_run_dir / "artifacts" / "model_meta.json").exists()


def test_load_model_from_mlflow_uri_version_form(monkeypatch, tmp_path, _reset_loader):
    fake_run_dir = _write_fake_run(tmp_path / "run")

    fake_mv = MagicMock(run_id="run-abc", version="7")
    fake_client = MagicMock()
    fake_client.get_model_version.return_value = fake_mv

    with patch("mlflow.MlflowClient", return_value=fake_client), \
         patch("mlflow.artifacts.download_artifacts", return_value=str(fake_run_dir)):
        monkeypatch.setenv("MLFLOW_MODEL_URI", "models:/fraud_model/7")

        model, _ = loader.load_model()

    fake_client.get_model_version.assert_called_once_with("fraud_model", "7")
    assert isinstance(model, LogisticRegression)


def test_mlflow_uri_takes_precedence_over_model_path(monkeypatch, tmp_path, _reset_loader):
    """When BOTH env vars are set, MLFLOW_MODEL_URI wins."""
    fake_run_dir = _write_fake_run(tmp_path / "run", model_name="registry_model")

    fake_client = MagicMock()
    fake_client.get_model_version_by_alias.return_value = MagicMock(run_id="r", version="1")

    with patch("mlflow.MlflowClient", return_value=fake_client), \
         patch("mlflow.artifacts.download_artifacts", return_value=str(fake_run_dir)):
        monkeypatch.setenv("MLFLOW_MODEL_URI", "models:/fraud_model@production")
        # Set MODEL_PATH to a bogus location that would fail if it were used
        monkeypatch.setenv("MODEL_PATH", "/nonexistent/should-not-be-read.pkl")

        model, _ = loader.load_model()

    assert isinstance(model, LogisticRegression), (
        "MLFLOW_MODEL_URI must be preferred over MODEL_PATH"
    )


def test_stage_raises_when_no_base_pkl(monkeypatch, tmp_path, _reset_loader):
    """A run with ONLY prep/calibrated pkls is a broken registry entry — fail loudly."""
    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    joblib.dump(LogisticRegression(), root / "artifacts" / "fraud_model_prep.pkl")

    fake_client = MagicMock()
    fake_client.get_model_version_by_alias.return_value = MagicMock(run_id="r", version="1")

    with patch("mlflow.MlflowClient", return_value=fake_client), \
         patch("mlflow.artifacts.download_artifacts", return_value=str(root)):
        monkeypatch.setenv("MLFLOW_MODEL_URI", "models:/fraud_model@production")

        with pytest.raises(RuntimeError, match="base"):
            loader.load_model()
