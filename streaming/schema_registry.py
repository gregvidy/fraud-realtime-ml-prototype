"""
streaming/schema_registry.py
----------------------------
Registers Avro schemas with the Redpanda Schema Registry and looks them up.
Uses the Confluent-compatible HTTP API that Redpanda's built-in Schema
Registry exposes on port 8081.

Subject naming follows the Confluent default: `<topic>-value` (we don't
register key schemas — keys are plain UTF-8 user_id strings).

Compatibility mode: BACKWARD (default). Producers can add optional fields
with defaults; consumers keep working. Breaking changes require a new topic
or a subject rename.

Usage (via Makefile):
    make stream-schemas               # register all
    make stream-schemas-list          # list subjects + versions

Direct:
    python -m streaming.schema_registry register
    python -m streaming.schema_registry list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from streaming.config import (
    CHANNELS,
    LOGIN_EVENT_SCHEMA_PATH,
    LOGIN_EVENTS_TOPIC,
    RAW_TXN_TOPICS,
    SCHEMA_REGISTRY_URL,
    SCORED_TXN_EVENT_SCHEMA_PATH,
    TXN_EVENT_SCHEMA_PATH,
    TXN_SCORED_TOPIC,
    value_subject,
)

# Subject → local schema file mapping.
SUBJECT_SCHEMAS: dict[str, Path] = {
    # All 6 channel raw topics share the TxnEvent schema (same event shape,
    # differentiated only by which topic they're written to).
    **{value_subject(RAW_TXN_TOPICS[ch]): TXN_EVENT_SCHEMA_PATH for ch in CHANNELS},
    value_subject(TXN_SCORED_TOPIC): SCORED_TXN_EVENT_SCHEMA_PATH,
    value_subject(LOGIN_EVENTS_TOPIC): LOGIN_EVENT_SCHEMA_PATH,
}


def _post_schema(client: httpx.Client, subject: str, schema_body: dict) -> dict:
    """POST a new schema version. Idempotent: reposting an identical schema
    returns the same id/version."""
    resp = client.post(
        f"/subjects/{subject}/versions",
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        json={"schema": json.dumps(schema_body), "schemaType": "AVRO"},
    )
    resp.raise_for_status()
    return resp.json()


def _set_compat(client: httpx.Client, subject: str, mode: str = "BACKWARD") -> None:
    """Set per-subject compatibility so schema evolution is enforced."""
    resp = client.put(
        f"/config/{subject}",
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        json={"compatibility": mode},
    )
    resp.raise_for_status()


def cmd_register() -> int:
    print(f"Registering {len(SUBJECT_SCHEMAS)} subjects against {SCHEMA_REGISTRY_URL}")
    with httpx.Client(base_url=SCHEMA_REGISTRY_URL, timeout=10.0) as client:
        # Cache schema-body objects so we only read each .avsc once.
        schema_cache: dict[Path, dict] = {}
        for subject, path in SUBJECT_SCHEMAS.items():
            if path not in schema_cache:
                schema_cache[path] = json.loads(path.read_text())
            result = _post_schema(client, subject, schema_cache[path])
            _set_compat(client, subject)
            print(f"  {subject:<40s}  id={result['id']}  ({path.name})")
    return 0


def cmd_list() -> int:
    with httpx.Client(base_url=SCHEMA_REGISTRY_URL, timeout=10.0) as client:
        subjects = client.get("/subjects").json()
        print(f"── {len(subjects)} subjects ──")
        for s in sorted(subjects):
            versions = client.get(f"/subjects/{s}/versions").json()
            latest = client.get(f"/subjects/{s}/versions/latest").json()
            print(f"  {s:<40s}  versions={versions}  latest_id={latest['id']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Avro schemas in Redpanda Schema Registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("register", help="POST all Avro schemas from streaming/schemas/*.avsc")
    sub.add_parser("list", help="List registered subjects + versions")
    args = parser.parse_args()

    if args.cmd == "register":
        return cmd_register()
    if args.cmd == "list":
        return cmd_list()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
