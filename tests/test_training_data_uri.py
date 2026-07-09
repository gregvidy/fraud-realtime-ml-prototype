"""Unit test for training.train_model._resolve_data_path (B5b)."""

from pathlib import Path

from training.train_model import _PROJECT_ROOT, _resolve_data_path


def test_env_override_takes_precedence_over_config():
    got = _resolve_data_path("training/datasets/local.parquet", env_override="s3://bucket/x.parquet")
    assert got == "s3://bucket/x.parquet"


def test_s3_uri_returned_verbatim():
    got = _resolve_data_path("s3://fraudml-data/training/datasets/training_dataset.parquet", env_override=None)
    assert got == "s3://fraudml-data/training/datasets/training_dataset.parquet"


def test_relative_config_path_becomes_project_root_absolute():
    got = _resolve_data_path("training/datasets/training_dataset.parquet", env_override=None)
    assert isinstance(got, Path)
    assert got == _PROJECT_ROOT / "training" / "datasets" / "training_dataset.parquet"
    assert got.is_absolute()


def test_absolute_config_path_kept_as_is():
    abs_path = "/tmp/some_parquet.parquet"
    got = _resolve_data_path(abs_path, env_override=None)
    assert got == Path(abs_path)


def test_empty_env_override_falls_back_to_config():
    """os.getenv returns '' when the var is set-but-empty; treat that as unset."""
    got = _resolve_data_path("training/datasets/local.parquet", env_override="")
    assert got == _PROJECT_ROOT / "training" / "datasets" / "local.parquet"
