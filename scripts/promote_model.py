"""
scripts/promote_model.py
-------------------------
Promote a trained model from MLflow to be the active model for the /score API.

After choosing the best run in the MLflow UI (make mlflow-ui), copy that run's
artifacts into models/, update model_meta.json, write MODEL_PATH to .env, and
tag the run/version as "champion" in MLflow.

Usage:
    # List recent runs with key metrics
    python scripts/promote_model.py --list

    # Promote a specific run (paste run_id from MLflow UI)
    python scripts/promote_model.py --run-id <RUN_ID>

    # Promote with a custom alias tag (default: champion)
    python scripts/promote_model.py --run-id <RUN_ID> --alias production

    # Promote a registered model version
    python scripts/promote_model.py --model-name fraud_model --version 3

    # Dry-run — show what would happen without changing anything
    python scripts/promote_model.py --run-id <RUN_ID> --dry-run

After promotion, restart the API to load the new model:
    make stop-api && make start-api
    — or —  make start-api-dev
"""

import argparse
import json
import math
import os
import shutil
from pathlib import Path

import mlflow

_PROJECT_ROOT = Path(__file__).parent.parent
_MODELS_DIR = _PROJECT_ROOT / "models"
_ENV_FILE = _PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_tracking_uri() -> str:
    """Return the MLflow tracking URI, resolving bare relative paths."""
    uri = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
    if "://" not in uri:
        uri = f"sqlite:///{(_PROJECT_ROOT / uri).resolve()}"
    return uri


def _set_tracking_uri() -> str:
    uri = _resolve_tracking_uri()
    mlflow.set_tracking_uri(uri)
    return uri


def _update_env(key: str, value: str) -> None:
    """Upsert a KEY=value line in .env (preserves all other lines)."""
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text().splitlines(keepends=True)
        found = False
        for i, line in enumerate(lines):
            stripped = line.split("=", 1)[0].strip()
            if stripped == key:
                lines[i] = f"{key}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}\n")
        _ENV_FILE.write_text("".join(lines))
    else:
        _ENV_FILE.write_text(f"{key}={value}\n")


def _fmt(val: float) -> str:
    return f"{val:.4f}" if not math.isnan(val) else "   —  "


# ---------------------------------------------------------------------------
# list runs
# ---------------------------------------------------------------------------

def list_runs(n: int = 15) -> None:
    """Print a summary table of recent MLflow runs with key metrics."""
    _set_tracking_uri()
    client = mlflow.MlflowClient()

    experiments = client.search_experiments()
    if not experiments:
        print("No MLflow experiments found. Run 'make train' first.")
        return

    header = (
        f"{'RUN ID':>12}  {'RUN NAME':<35} {'MODEL TYPE':<14} "
        f"{'ROC-AUC':>8} {'PR-AUC':>8} {'THRESHOLD':>10}  CHAMPION"
    )
    sep = "-" * len(header)
    print(f"\n{header}")
    print(sep)

    for exp in experiments:
        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            order_by=["start_time DESC"],
            max_results=n,
        )
        for run in runs:
            m = run.data.metrics
            tags = run.data.tags

            roc = m.get("eval.roc_auc", m.get("train.val_roc_auc", float("nan")))
            pr  = m.get("eval.pr_auc",  m.get("train.val_pr_auc",  float("nan")))
            thr = m.get("eval.threshold", m.get("train.threshold", float("nan")))

            model_type = tags.get("mlflow.runName", "").split("_")[0]
            if not model_type:
                model_type = run.data.params.get("model.type", "—")

            champion = "✓" if tags.get("champion") == "true" else ""
            run_id_short = run.info.run_id[:12]

            print(
                f"{run_id_short}  {(run.info.run_name or ''):>35} {model_type:<14} "
                f"{_fmt(roc):>8} {_fmt(pr):>8} {_fmt(thr):>10}  {champion}"
            )

    print()
    print("Use the first 12 chars (or full) run ID with --run-id to promote a model.")
    print("Example:  make promote-model RUN_ID=<run_id_here>")


# ---------------------------------------------------------------------------
# Promote from run ID
# ---------------------------------------------------------------------------

def promote_from_run(run_id: str, alias: str, dry_run: bool) -> None:
    tracking_uri = _set_tracking_uri()
    client = mlflow.MlflowClient()

    # Resolve a 12-char prefix to the full run ID if needed
    if len(run_id) < 32:
        experiments = client.search_experiments()
        matched = []
        for exp in experiments:
            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                max_results=200,
            )
            matched.extend(r for r in runs if r.info.run_id.startswith(run_id))
        if len(matched) == 0:
            raise SystemExit(f"ERROR: No run found with prefix '{run_id}'.")
        if len(matched) > 1:
            ids = [r.info.run_id for r in matched]
            raise SystemExit(f"ERROR: Prefix '{run_id}' is ambiguous — matches:\n  " + "\n  ".join(ids))
        run_id = matched[0].info.run_id

    run = client.get_run(run_id)
    print(f"\nRun ID   : {run_id}")
    print(f"Run name : {run.info.run_name}")
    print(f"Status   : {run.info.status}")

    m = run.data.metrics
    roc = m.get("eval.roc_auc", m.get("train.val_roc_auc", float("nan")))
    pr  = m.get("eval.pr_auc",  m.get("train.val_pr_auc",  float("nan")))
    print(f"ROC-AUC  : {_fmt(roc)}    PR-AUC: {_fmt(pr)}")

    tmp_dir = _MODELS_DIR / f"_promote_tmp_{run_id[:8]}"
    try:
        print(f"\nDownloading artifacts from run {run_id[:8]}...")

        # Download model_meta.json to discover the output_name
        try:
            meta_local = client.download_artifacts(run_id, "config/model_meta.json", str(tmp_dir))
        except Exception:
            raise SystemExit(
                "ERROR: Could not download 'config/model_meta.json' from this run.\n"
                "This run may not have been logged by train_model.py, or artifacts may be missing."
            )
        meta = json.loads(Path(meta_local).read_text())
        output_name = meta["model_name"]
        print(f"Model name : {output_name}  ({meta.get('model_type', '?')})")
        print(f"Val ROC-AUC: {meta.get('val_roc_auc', '—')}  Val PR-AUC: {meta.get('val_pr_auc', '—')}")
        print(f"Threshold  : {meta.get('threshold', '—')}")
        print(f"Calibration: {meta.get('calibration_method', 'none')}")

        if dry_run:
            print(f"\n[DRY RUN] Would promote model '{output_name}' from run {run_id[:8]}")
            print(f"[DRY RUN] Artifacts → {_MODELS_DIR}/")
            print(f"[DRY RUN] .env MODEL_PATH → models/{output_name}.pkl")
            print(f"[DRY RUN] MLflow run tagged → champion=true, promoted_as={alias}")
            return

        # Download all pkl artifacts
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        artifacts_dir = client.download_artifacts(run_id, "artifacts", str(tmp_dir))
        artifacts_path = Path(artifacts_dir)

        pkl_files = list(artifacts_path.glob("*.pkl"))
        if not pkl_files:
            raise SystemExit(
                "ERROR: No .pkl artifacts found under 'artifacts/' in this run.\n"
                "Re-run training — train_model.py logs pkl files as MLflow artifacts."
            )

        print()
        for src in sorted(pkl_files):
            dst = _MODELS_DIR / src.name
            shutil.copy2(src, dst)
            print(f"  Copied: {src.name}")

        # Update model_meta.json (always at models/model_meta.json)
        meta_dst = _MODELS_DIR / "model_meta.json"
        shutil.copy2(meta_local, meta_dst)
        print(f"  Copied: model_meta.json")

        # Determine the active scoring model path (prefer calibrated artifact)
        calib_path = _MODELS_DIR / f"{output_name}_calibrated.pkl"
        base_path  = _MODELS_DIR / f"{output_name}.pkl"
        active_model_path = calib_path if calib_path.exists() else base_path

        # Persist MODEL_PATH into .env so the API picks up the promoted model
        rel_path = active_model_path.relative_to(_PROJECT_ROOT)
        _update_env("MODEL_PATH", str(rel_path))
        print(f"  .env  : MODEL_PATH={rel_path}")

        # Tag the MLflow run as champion
        client.set_tag(run_id, "champion", "true")
        client.set_tag(run_id, "promoted_as", alias)
        print(f"  MLflow: run tagged champion=true, promoted_as={alias}")

        # Clear the champion tag from any previously promoted runs
        all_experiments = client.search_experiments()
        for exp in all_experiments:
            prev_runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                filter_string="tags.champion = 'true'",
                max_results=100,
            )
            for prev in prev_runs:
                if prev.info.run_id != run_id:
                    client.set_tag(prev.info.run_id, "champion", "false")

        # Try to set alias on the registered model version
        try:
            versions = client.search_model_versions(f"run_id='{run_id}'")
            for mv in versions:
                try:
                    client.set_registered_model_alias(mv.name, alias, mv.version)
                    print(f"  MLflow: registry alias '{alias}' → {mv.name} v{mv.version}")
                except Exception:
                    pass
                client.set_model_version_tag(mv.name, mv.version, "champion", "true")
        except Exception:
            pass  # Registry not used — that's fine

        print(f"\n✓ Model '{output_name}' promoted successfully!")
        print(f"  Active model : {active_model_path.relative_to(_PROJECT_ROOT)}")
        print(f"  Tracking URI : {tracking_uri}")
        print()
        print("Restart the API to activate the promoted model:")
        print("  make stop-api && make start-api")
        print("  — or —  make start-api-dev  (for dev with hot-reload)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Promote from registry name + version
# ---------------------------------------------------------------------------

def promote_from_registry(model_name: str, version: int, alias: str, dry_run: bool) -> None:
    _set_tracking_uri()
    client = mlflow.MlflowClient()

    try:
        mv = client.get_model_version(model_name, str(version))
    except Exception as exc:
        raise SystemExit(
            f"ERROR: Registered model '{model_name}' version {version} not found.\n"
            f"  MLflow: {exc}\n"
            f"  Use 'make list-models' to see available runs."
        ) from exc

    print(f"\nRegistry model : {model_name}  v{version}")
    print(f"Run ID         : {mv.run_id}")

    promote_from_run(mv.run_id, alias=alias, dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Promote a trained MLflow model to be the active model for the /score API.\n\n"
            "Examples:\n"
            "  python scripts/promote_model.py --list\n"
            "  python scripts/promote_model.py --run-id abc123\n"
            "  python scripts/promote_model.py --model-name fraud_model --version 3\n"
            "  python scripts/promote_model.py --run-id abc123 --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List recent MLflow runs with key metrics and exit",
    )
    parser.add_argument(
        "-n", type=int, default=15, metavar="N",
        help="Number of recent runs to show with --list (default: 15)",
    )
    parser.add_argument(
        "--run-id", metavar="RUN_ID",
        help="MLflow run ID to promote (full or first 12 chars)",
    )
    parser.add_argument(
        "--model-name", metavar="NAME",
        help="Registered model name (use with --version)",
    )
    parser.add_argument(
        "--version", type=int, metavar="VERSION",
        help="Registered model version to promote",
    )
    parser.add_argument(
        "--alias", default="champion", metavar="ALIAS",
        help="MLflow alias / tag to assign to the promoted model (default: champion)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without making any changes",
    )

    args = parser.parse_args()

    if args.list:
        list_runs(n=args.n)
    elif args.run_id:
        promote_from_run(args.run_id, alias=args.alias, dry_run=args.dry_run)
    elif args.model_name and args.version is not None:
        promote_from_registry(args.model_name, args.version, alias=args.alias, dry_run=args.dry_run)
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  make list-models              # see available runs")
        print("  make promote-model RUN_ID=<id>  # promote a run")


if __name__ == "__main__":
    main()
