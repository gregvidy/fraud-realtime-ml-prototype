"""
data_sources.py — Feast data source definitions backed by Parquet snapshots.

dbt (`--target clickhouse`) builds feature tables in ClickHouse `main` schema.
scripts/materialize_features.py exports those tables to Parquet via Arrow.
Feast reads these Parquet files as its offline store during historical
retrieval and as the source for materializing features into Redis (online).

Parquet files live at:
    data/parquet/fct_user_features_v1.parquet
    data/parquet/fct_device_features_v1.parquet
    data/parquet/fct_merchant_features_v1.parquet
"""

import os
from pathlib import Path

from feast import FileSource

_REPO_ROOT = Path(__file__).parents[2]
_PARQUET_DIR = _REPO_ROOT / "data" / "parquet"

# Respect override via env var (useful for CI or custom paths)
_parquet_dir = Path(os.getenv("PARQUET_DIR", str(_PARQUET_DIR)))

user_features_source = FileSource(
    name="user_features_source",
    path=str(_parquet_dir / "fct_user_features_v1.parquet"),
    timestamp_field="event_timestamp",
)

device_features_source = FileSource(
    name="device_features_source",
    path=str(_parquet_dir / "fct_device_features_v1.parquet"),
    timestamp_field="event_timestamp",
)

merchant_features_source = FileSource(
    name="merchant_features_source",
    path=str(_parquet_dir / "fct_merchant_features_v1.parquet"),
    timestamp_field="event_timestamp",
)
