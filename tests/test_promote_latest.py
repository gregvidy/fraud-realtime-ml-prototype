"""Unit tests for scripts/promote_latest.py (B5c)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# Add scripts/ to sys.path so we can import the module directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import promote_latest  # noqa: E402


def test_latest_version_picks_highest_numeric_version():
    versions = [
        SimpleNamespace(version="1"),
        SimpleNamespace(version="10"),
        SimpleNamespace(version="2"),
    ]
    client = MagicMock()
    client.search_model_versions.return_value = versions
    assert promote_latest.latest_version(client, "fraud") == "10"
    client.search_model_versions.assert_called_once_with("name='fraud'")


def test_latest_version_raises_when_no_versions():
    client = MagicMock()
    client.search_model_versions.return_value = []
    with pytest.raises(RuntimeError, match="No versions registered"):
        promote_latest.latest_version(client, "fraud")


def test_main_sets_alias_on_latest(monkeypatch, capsys):
    versions = [SimpleNamespace(version="3"), SimpleNamespace(version="7")]
    fake_client = MagicMock()
    fake_client.search_model_versions.return_value = versions

    monkeypatch.setattr(sys, "argv", [
        "promote_latest",
        "--model", "lgbm_fraud_model",
        "--alias", "production",
    ])

    with patch.object(promote_latest, "mlflow") as mock_mlflow:
        mock_mlflow.MlflowClient.return_value = fake_client
        rc = promote_latest.main()

    assert rc == 0
    fake_client.set_registered_model_alias.assert_called_once_with(
        "lgbm_fraud_model", "production", "7",
    )
    captured = capsys.readouterr()
    assert "lgbm_fraud_model v7" in captured.out
    assert "production" in captured.out


def test_main_defaults_alias_to_production(monkeypatch):
    fake_client = MagicMock()
    fake_client.search_model_versions.return_value = [SimpleNamespace(version="1")]

    monkeypatch.setattr(sys, "argv", ["promote_latest", "--model", "foo"])

    with patch.object(promote_latest, "mlflow") as mock_mlflow:
        mock_mlflow.MlflowClient.return_value = fake_client
        promote_latest.main()

    fake_client.set_registered_model_alias.assert_called_once_with("foo", "production", "1")


def test_main_honors_tracking_uri_flag(monkeypatch):
    fake_client = MagicMock()
    fake_client.search_model_versions.return_value = [SimpleNamespace(version="1")]

    monkeypatch.setattr(sys, "argv", [
        "promote_latest",
        "--model", "foo",
        "--tracking-uri", "http://mlflow.example:5000",
    ])

    with patch.object(promote_latest, "mlflow") as mock_mlflow:
        mock_mlflow.MlflowClient.return_value = fake_client
        promote_latest.main()

    mock_mlflow.set_tracking_uri.assert_called_once_with("http://mlflow.example:5000")
