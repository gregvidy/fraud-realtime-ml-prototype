"""
streaming/run.py
----------------
CLI dispatcher: `python -m streaming.run <consumer_name> [--duration SECS]`.

Available consumers:
  fraud_decisioning       — calls /score, publishes to txn.scored
  feature_store_updater   — Redis sliding-window updates
  postgres_sink           — batched INSERTs into raw_transactions
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from streaming.consumers import (
    feature_store_updater,
    fraud_decisioning,
    postgres_sink,
)

CONSUMERS = {
    "fraud_decisioning":     fraud_decisioning.run,
    "feature_store_updater": feature_store_updater.run,
    "postgres_sink":         postgres_sink.run,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a streaming consumer")
    parser.add_argument("name", choices=sorted(CONSUMERS))
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Stop after N seconds (default: 0 = run forever until Ctrl+C)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(CONSUMERS[args.name](duration=args.duration))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
