"""fraudml CLI — Feature Registry commands.

Usage:
    python -m fraudml.feature_registry.cli features list [--service NAME]
    python -m fraudml.feature_registry.cli features describe <name>
    python -m fraudml.feature_registry.cli features validate
    python -m fraudml.feature_registry.cli features services

The registry is loaded from `$FRAUDML_FEATURE_DEFS` if set, else from
`feature_definitions/` relative to the repo root (detected by walking up
from cwd until a `feature_definitions/` directory is found).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .loader import load_registry
from .validator import validate


def _find_defs_dir() -> Path:
    env = os.environ.get("FRAUDML_FEATURE_DEFS")
    if env:
        return Path(env)
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "feature_definitions").is_dir():
            return candidate / "feature_definitions"
    raise FileNotFoundError(
        "feature_definitions/ not found. cd to the repo or set FRAUDML_FEATURE_DEFS."
    )


def _cmd_list(reg, args: argparse.Namespace) -> int:
    names = (
        [f.name for f in reg.resolve_service(args.service)]
        if args.service
        else reg.list_features()
    )
    for n in names:
        f = reg.features[n]
        print(f"{f.name:<40} {f.mode:<10} {f.dtype:<6} entity={f.entity or '-'}")
    print(f"\n{len(names)} feature(s)")
    return 0


def _cmd_describe(reg, args: argparse.Namespace) -> int:
    if args.name not in reg.features:
        print(f"ERROR: no such feature {args.name!r}", file=sys.stderr)
        return 1
    f = reg.features[args.name]
    print(f"name:        {f.name}")
    print(f"mode:        {f.mode}")
    print(f"dtype:       {f.dtype}")
    print(f"entity:      {f.entity or '(none)'}")
    print(f"online:      {f.online}")
    print(f"version:     {f.version}")
    print(f"default:     {f.default!r}")
    print(f"description: {f.description}")
    print("source:")
    for k, v in f.source.items():
        print(f"  {k}: {v!r}")
    return 0


def _cmd_validate(reg, args: argparse.Namespace) -> int:
    errors = validate(reg)
    if errors:
        for e in errors:
            print(f"ERROR  {e}", file=sys.stderr)
        print(f"\n{len(errors)} error(s)", file=sys.stderr)
        return 1
    print(
        f"OK  {len(reg.entities)} entities, {len(reg.features)} features, "
        f"{len(reg.services)} services"
    )
    return 0


def _cmd_services(reg, args: argparse.Namespace) -> int:
    for name in reg.list_services():
        svc = reg.services[name]
        print(f"{name:<20} v{svc.version:<4} {len(svc.features)} features")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fraudml")
    subs = parser.add_subparsers(dest="cmd", required=True)

    features = subs.add_parser("features", help="Feature registry commands")
    fsubs = features.add_subparsers(dest="fs_cmd", required=True)

    p_list = fsubs.add_parser("list", help="List all features (or a service)")
    p_list.add_argument("--service", help="List features in a named feature_service (ordered)")

    p_desc = fsubs.add_parser("describe", help="Show one feature's metadata")
    p_desc.add_argument("name")

    fsubs.add_parser("validate", help="Syntactic validation of the registry")
    fsubs.add_parser("services", help="List feature_services")

    args = parser.parse_args(argv)
    reg = load_registry(_find_defs_dir())

    return {
        "list": _cmd_list,
        "describe": _cmd_describe,
        "validate": _cmd_validate,
        "services": _cmd_services,
    }[args.fs_cmd](reg, args)


if __name__ == "__main__":
    raise SystemExit(main())
