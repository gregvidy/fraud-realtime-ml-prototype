"""Training pipeline — KFP v2 DAG.

Chains the three primary training scripts as container components:
    1. build_dataset  → training/build_training_dataset.py
    2. train          → training/train_model.py
    3. evaluate       → training/evaluate_model.py

Promotion (scripts/promote_model.py) is intentionally NOT part of the pipeline
in B2 — it needs an MLflow run_id that KFP v2 doesn't expose via a simple
string placeholder. A follow-up slice will add a `promote_op` that reads the
run_id from an MLflow output artifact produced by `train`. For now, users
promote manually via `make promote-model RUN_ID=<id>` after inspecting MLflow.

Design decisions:
    - Each component runs the SAME image (`fraudml/training`), just a different
      `python -m ...` command. Simpler than 4 per-component images.
    - Existing scripts are called unchanged (Karpathy #3). Their I/O contract:
        * config file at /app/training/experiments/<config>.yaml
        * dataset parquet at /app/data/parquet/fct_training_dataset.parquet
        * model artifacts at /app/models/*.pkl + model_meta.json
        * MLflow tracking via MLFLOW_TRACKING_URI env var
    - The Feature Registry (B1) is exposed to components via FRAUDML_FEATURE_DEFS
      env var pointing to /app/feature_definitions/. Scripts don't consume it yet
      — Phase C will retrofit them to resolve the fraud_v1 service from the
      registry instead of the legacy feature_contract.yaml.
    - The components pass `--config <path>` and `--feature-service <name>` to the
      training scripts. The current training scripts DO NOT yet accept these
      flags; they resolve config paths internally. Retrofitting them is a Phase C
      task. At B2 the pipeline compiles, tests pass, and the container spec is
      the target contract — runtime submission will not succeed until scripts
      are adapted (see success criteria in the B2 commit message).

Not covered in B2:
    - Actual runtime submission with populated data. Requires the training image
      to be built + `k3d image import`'d, plus training data available on a PVC.
      Both are separate slices.
    - Promote step (see paragraph above).
"""

# NOTE: do NOT add `from __future__ import annotations` here. KFP v2 SDK
# introspects function annotations at runtime; PEP-563 string annotations
# make it treat `x: str` as an artifact schema string, breaking compile.

from kfp import dsl

# Image tag used by all three components. Built by `deploy/Dockerfile.train` and
# imported into k3d via `make pipeline-image-import`. When cluster gains a real
# registry, replace with a fully-qualified reference like
# `registry.local:5000/fraudml/training:v1`.
TRAINING_IMAGE = "fraudml/training:v1"


# ---------------------------------------------------------------------------
# Components — each is a thin wrapper around an existing training script.
# ---------------------------------------------------------------------------


@dsl.container_component
def build_dataset(
    config_path: str,
    feature_service: str,
) -> dsl.ContainerSpec:
    """Build the training parquet dataset from ClickHouse via dbt+Feast+builder.

    Consumes: config YAML (dataset section), Feature Registry.
    Produces: /app/data/parquet/fct_training_dataset.parquet (on component pod).
    """
    return dsl.ContainerSpec(
        image=TRAINING_IMAGE,
        command=["python", "-m", "training.build_training_dataset"],
        args=["--config", config_path],
    )


@dsl.container_component
def train(
    config_path: str,
    feature_service: str,
) -> dsl.ContainerSpec:
    """Train the model per config. Logs run to MLflow."""
    return dsl.ContainerSpec(
        image=TRAINING_IMAGE,
        command=["python", "-m", "training.train_model"],
        args=["--config", config_path],
    )


@dsl.container_component
def evaluate(
    config_path: str,
) -> dsl.ContainerSpec:
    """Evaluate the trained model (holdout metrics, calibration curves, SHAP).

    Reads model artifacts from /app/models/, logs evaluation metrics + plots
    to the same MLflow run as `train`.
    """
    return dsl.ContainerSpec(
        image=TRAINING_IMAGE,
        command=["python", "-m", "training.evaluate_model"],
        args=["--config", config_path],
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

# Env vars are hardcoded rather than pipeline params. kfp 2.7 `set_env_variable`
# only accepts raw strings, not PipelineParameterChannel. These values are stable
# (in-cluster DNS + image mount path) so parameterization would be over-engineering.
MLFLOW_TRACKING_URI = "http://mlflow.data-plane.svc.cluster.local:5000"
FEATURE_DEFS_DIR = "/app/feature_definitions"

# B5c: MinIO wiring. Each component reads training parquet from MinIO instead
# of the image-baked data/. POC creds match the minio-root Secret in
# infra/k8s/bootstrap/data-plane/minio.yaml — swap for IRSA / secret injection
# via kfp-kubernetes when moving beyond POC.
TRAINING_DATA_URI = "s3://fraudml-data/training/datasets/training_dataset.parquet"
S3_ENDPOINT_URL = "http://minio.data-plane.svc.cluster.local:9000"
S3_ACCESS_KEY = "minioadmin"
S3_SECRET_KEY = "minioadmin"
S3_REGION = "us-east-1"


def _env(task) -> None:
    """Set every env var each training component needs (MLflow + Feature Registry + MinIO)."""
    task.set_env_variable("MLFLOW_TRACKING_URI", MLFLOW_TRACKING_URI)
    task.set_env_variable("FRAUDML_FEATURE_DEFS", FEATURE_DEFS_DIR)
    task.set_env_variable("TRAINING_DATA_URI", TRAINING_DATA_URI)
    task.set_env_variable("AWS_ENDPOINT_URL_S3", S3_ENDPOINT_URL)
    task.set_env_variable("AWS_ACCESS_KEY_ID", S3_ACCESS_KEY)
    task.set_env_variable("AWS_SECRET_ACCESS_KEY", S3_SECRET_KEY)
    task.set_env_variable("AWS_REGION", S3_REGION)


@dsl.pipeline(
    name="fraudml-training",
    description=(
        "End-to-end fraud model training: build dataset → train → evaluate. "
        "Reads Feature Registry via FRAUDML_FEATURE_DEFS."
    ),
)
def training_pipeline(
    config_path: str = "experiments/lgbm_v1.yaml",
    feature_service: str = "fraud_v1",
):
    """Chain: build → train → evaluate."""

    build_task = build_dataset(
        config_path=config_path,
        feature_service=feature_service,
    )
    build_task.set_display_name("build_dataset")
    _env(build_task)

    train_task = train(
        config_path=config_path,
        feature_service=feature_service,
    )
    train_task.set_display_name("train")
    train_task.after(build_task)
    _env(train_task)

    eval_task = evaluate(
        config_path=config_path,
    )
    eval_task.set_display_name("evaluate")
    eval_task.after(train_task)
    _env(eval_task)
    # B6: evaluate needs to fetch the just-trained model from MLflow (each KFP
    # step has its own filesystem). train_model.py self-aliases the new version
    # as 'candidate' (see training/experiments/lgbm_v1.yaml mlflow.model_alias);
    # evaluate resolves that alias here.
    eval_task.set_env_variable(
        "MLFLOW_MODEL_URI", "models:/lgbm_fraud_model@candidate"
    )
