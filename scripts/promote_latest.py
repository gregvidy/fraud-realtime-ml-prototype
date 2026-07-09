"""Set a registered-model alias on the LATEST model version (B5c).

Small wrapper around ``MlflowClient.set_registered_model_alias`` — finds the
highest-numbered version of ``--model`` and points ``--alias`` at it. Used by
``make promote-latest`` in the bootstrap chain so a fresh training run
automatically becomes the ISVC's ``production`` target.

Usage:
    python scripts/promote_latest.py --model lgbm_fraud_model --alias production
    python scripts/promote_latest.py --model lgbm_fraud_model  # defaults --alias to 'production'
"""

from __future__ import annotations

import argparse
import sys

import mlflow


def latest_version(client: "mlflow.MlflowClient", model_name: str) -> str:
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        raise RuntimeError(
            f"No versions registered for model {model_name!r}. Run `make train` first."
        )
    # search_model_versions returns strings for version, so cast for numeric sort
    return max(versions, key=lambda mv: int(mv.version)).version


def main() -> int:
    parser = argparse.ArgumentParser(description="Alias the latest model version.")
    parser.add_argument("--model", required=True, help="Registered model name.")
    parser.add_argument("--alias", default="production", help="Alias to set (default: production).")
    parser.add_argument(
        "--tracking-uri", default=None,
        help="Override MLFLOW_TRACKING_URI (else read from env / config).",
    )
    args = parser.parse_args()

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)

    client = mlflow.MlflowClient()
    version = latest_version(client, args.model)
    client.set_registered_model_alias(args.model, args.alias, version)
    print(f"[promote-latest] {args.model} v{version} → alias={args.alias!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
