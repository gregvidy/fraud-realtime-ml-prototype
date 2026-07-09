"""Test for evaluate_model._stage_from_mlflow (B6 KFP model fetch)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import joblib
import pytest
from sklearn.linear_model import LogisticRegression

from training.evaluate_model import _stage_from_mlflow


def _write_fake_run(root: Path) -> None:
    """Mirror training/train_model.py's artifact layout: pkls under artifacts/,
    model_meta.json under config/."""
    (root / "artifacts").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    joblib.dump(LogisticRegression(), root / "artifacts" / "lgbm_fraud_model.pkl")
    meta = {"model_name": "lgbm_fraud_model", "threshold": 0.5, "feature_cols": []}
    (root / "config" / "model_meta.json").write_text(json.dumps(meta))


def test_stage_from_mlflow_alias_copies_meta_into_artifacts_dir(tmp_path):
    _write_fake_run(tmp_path / "run")

    fake_client = MagicMock()
    fake_client.get_model_version_by_alias.return_value = MagicMock(
        run_id="r-abc", version="3",
    )

    with patch("training.evaluate_model.mlflow") as mock_mlflow:
        mock_mlflow.MlflowClient.return_value = fake_client
        mock_mlflow.artifacts.download_artifacts.return_value = str(tmp_path / "run")

        meta_path = _stage_from_mlflow("models:/lgbm_fraud_model@candidate")

    fake_client.get_model_version_by_alias.assert_called_once_with(
        "lgbm_fraud_model", "candidate",
    )
    assert meta_path == tmp_path / "run" / "artifacts" / "model_meta.json"
    assert meta_path.exists(), "meta must have been copied from config/ into artifacts/"


def test_stage_from_mlflow_version_form(tmp_path):
    _write_fake_run(tmp_path / "run")

    fake_client = MagicMock()
    fake_client.get_model_version.return_value = MagicMock(run_id="r-xyz", version="7")

    with patch("training.evaluate_model.mlflow") as mock_mlflow:
        mock_mlflow.MlflowClient.return_value = fake_client
        mock_mlflow.artifacts.download_artifacts.return_value = str(tmp_path / "run")
        _stage_from_mlflow("models:/lgbm_fraud_model/7")

    fake_client.get_model_version.assert_called_once_with("lgbm_fraud_model", "7")


def test_stage_from_mlflow_rejects_bad_uri():
    with pytest.raises(ValueError, match="models:/"):
        _stage_from_mlflow("s3://not-a-model-uri")

    with pytest.raises(ValueError, match="alias"):
        _stage_from_mlflow("models:/bare_name")
