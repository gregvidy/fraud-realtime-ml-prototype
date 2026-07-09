"""Upload local training parquets into MinIO (B5b bootstrap).

Reads every ``.parquet`` under ``data/parquet/`` and ``training/datasets/``
and mirrors them to ``s3://<BUCKET>/<subdir>/<basename>``. Idempotent —
overwrites existing objects.

Environment (defaults suit ``make bootstrap-data`` against port-forwarded
MinIO):
  AWS_ENDPOINT_URL_S3   http://localhost:9000
  AWS_ACCESS_KEY_ID     minioadmin
  AWS_SECRET_ACCESS_KEY minioadmin
  AWS_REGION            us-east-1  (MinIO ignores it, but boto complains without one)
  BUCKET                fraudml-data

Not a general-purpose sync tool; this exists to seed a fresh cluster.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import s3fs


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = [
    REPO_ROOT / "data" / "parquet",
    REPO_ROOT / "training" / "datasets",
]


def _get_fs() -> s3fs.S3FileSystem:
    return s3fs.S3FileSystem(
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL_S3", "http://localhost:9000"),
        key=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
        secret=os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
    )


def main() -> int:
    bucket = os.environ.get("BUCKET", "fraudml-data")
    fs = _get_fs()

    uploaded = 0
    for src_dir in SOURCE_DIRS:
        if not src_dir.is_dir():
            print(f"[bootstrap-data] skipping — {src_dir.relative_to(REPO_ROOT)} not present")
            continue

        subkey = src_dir.relative_to(REPO_ROOT).as_posix()
        for path in sorted(src_dir.glob("*.parquet")):
            remote = f"{bucket}/{subkey}/{path.name}"
            print(f"[bootstrap-data] {path.relative_to(REPO_ROOT)}  →  s3://{remote}")
            fs.put_file(str(path), remote)
            uploaded += 1

    if uploaded == 0:
        print(
            "[bootstrap-data] nothing to upload — run 'make offline-pipeline' first "
            "to produce data/parquet/*.parquet and training/datasets/*.parquet",
            file=sys.stderr,
        )
        return 1

    print(f"[bootstrap-data] uploaded {uploaded} object(s) → s3://{bucket}/")
    print("[bootstrap-data] listing bucket:")
    for entry in fs.ls(bucket, refresh=True):
        print(f"  {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
