"""
data_sources.py — Feast data source definitions backed by dbt-generated tables.
"""

import os

from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)

_schema = os.getenv("POSTGRES_SCHEMA", "public")

user_features_source = PostgreSQLSource(
    name="user_features_source",
    query=f"SELECT * FROM {_schema}.fct_user_features",
    timestamp_field="event_timestamp",
)

device_features_source = PostgreSQLSource(
    name="device_features_source",
    query=f"SELECT * FROM {_schema}.fct_device_features",
    timestamp_field="event_timestamp",
)

merchant_features_source = PostgreSQLSource(
    name="merchant_features_source",
    query=f"SELECT * FROM {_schema}.fct_merchant_features",
    timestamp_field="event_timestamp",
)
