"""Katib trial wrapper — merges HP overrides into the base config then execs
``training/train_model.py``.

Katib injects hyperparameters as CLI arguments. This script:
  1. Loads the base experiment YAML (e.g. ``experiments/lgbm_v1.yaml``).
  2. Overrides the ``model.lightgbm.*`` block with values passed via ``--set``.
  3. Writes a temp config next to the base file.
  4. Execs ``python -m training.train_model --config <temp>``.

``train_model.py`` already prints ``PR-AUC=<float>`` (calibrated) at the end
of every run — Katib's stdOutMetricsCollector matches that line with the
regex declared in ``katib/experiments/lgbm_hpo.yaml``. No code changes to
``train_model.py`` are needed.

CLI:
    python -m training.katib_trial \\
        --config training/experiments/lgbm_v1.yaml \\
        --set model.lightgbm.num_leaves=63 \\
        --set model.lightgbm.learning_rate=0.05 \\
        --set model.lightgbm.n_estimators=500 \\
        --set model.lightgbm.subsample=0.8

Not designed to be imported. All meaningful surface is
:func:`apply_overrides` (unit-testable) and :func:`main` (CLI).
"""

from __future__ import annotations

import argparse
import copy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _coerce(value: str) -> Any:
    """Best-effort scalar coercion for CLI values (yaml.safe_load gives us
    ints / floats / bools / None correctly; falls back to str)."""
    return yaml.safe_load(value)


def apply_overrides(config: dict, overrides: list[str]) -> dict:
    """Return a deep-copied ``config`` with each ``dotted.key=value`` applied.

    Raises:
        ValueError: if an override lacks ``=`` or targets a non-existent key.
    """
    out = copy.deepcopy(config)
    for spec in overrides:
        if "=" not in spec:
            raise ValueError(f"override must be dotted.key=value, got: {spec!r}")
        key, raw = spec.split("=", 1)
        parts = key.split(".")

        cursor: Any = out
        for p in parts[:-1]:
            if not isinstance(cursor, dict) or p not in cursor:
                raise ValueError(f"override path {key!r} not found in base config")
            cursor = cursor[p]

        leaf = parts[-1]
        if not isinstance(cursor, dict) or leaf not in cursor:
            raise ValueError(f"override path {key!r} not found in base config")

        cursor[leaf] = _coerce(raw)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Katib trial wrapper for train_model.py")
    parser.add_argument("--config", type=Path, required=True, help="Base experiment YAML")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="dotted.key=value",
        help="Override a config value (repeatable). Values are YAML-parsed.",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        base = yaml.safe_load(f)

    merged = apply_overrides(base, args.set)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="katib_trial_", delete=False
    ) as tmp:
        yaml.safe_dump(merged, tmp, sort_keys=False)
        temp_path = tmp.name

    print(f"[katib_trial] running train_model.py with merged config → {temp_path}", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "training.train_model", "--config", temp_path],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
