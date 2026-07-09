"""CLI to compile the training pipeline to KFP IR YAML.

Usage:
    python -m fraudml.pipelines.compile [-o path/to/output.yaml]

Emits fraudml/pipelines/training_pipeline.yaml by default. The compiled YAML
is committed so reviewers can read it without installing kfp.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kfp.compiler import Compiler

from .training_pipeline import training_pipeline


DEFAULT_OUT = Path(__file__).parent / "training_pipeline.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fraudml-pipeline-compile")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output path (default: {DEFAULT_OUT.relative_to(Path.cwd()) if DEFAULT_OUT.is_relative_to(Path.cwd()) else DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    Compiler().compile(
        pipeline_func=training_pipeline,
        package_path=str(args.output),
    )
    print(f"compiled → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
