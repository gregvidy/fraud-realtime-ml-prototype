"""
materialize.py
--------------
Materializes the latest offline feature values into the Redis online store.
Run this after 'feast apply' and after dbt models are up-to-date.

Usage:
    python feast_repo/materialize.py [--days 2]
"""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from feast import FeatureStore

load_dotenv()

REPO_PATH = Path(__file__).parent / "feature_repo"


def main(days: int) -> None:
    store = FeatureStore(repo_path=str(REPO_PATH))

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    print(f"Materializing features  {start_dt.date()} → {end_dt.date()}")
    store.materialize(start_date=start_dt, end_date=end_dt)
    print("Materialization complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2)
    args = parser.parse_args()
    main(args.days)
