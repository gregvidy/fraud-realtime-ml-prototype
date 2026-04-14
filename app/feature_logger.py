"""
feature_logger.py
-----------------
Persists the exact online (Redis) feature values used at inference time to
the online_feature_log table in Postgres.

Design mirrors score_logger.py:
  - Fire-and-forget: logs a warning on failure, never raises.
  - Lazy singleton connection; reconnects if the connection is closed.
  - ON CONFLICT DO NOTHING prevents duplicate rows on retry storms.

At training time, build_training_dataset.py --use-feature-log joins this
table by transaction_id to replace dbt-derived online features with the
values the model actually saw, eliminating training-serving skew.
"""

import logging
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Online feature column names — must stay in sync with retriever.py output
# ---------------------------------------------------------------------------
ONLINE_FEATURE_COLS: tuple[str, ...] = (
    "user_txn_count_5m",
    "user_txn_count_10m",
    "user_txn_count_1h",
    "user_txn_amount_sum_5m",
    "user_txn_amount_sum_10m",
    "user_txn_amount_sum_1h",
    "user_distinct_merchants_5m",
    "user_distinct_merchants_10m",
    "user_distinct_merchants_1h",
    "user_failed_logins_15m",
    "user_failed_logins_1h",
    "device_txn_count_5m",
    "device_txn_count_10m",
    "device_txn_count_1h",
)

_conn = None

_INSERT = """
INSERT INTO online_feature_log (
    transaction_id, user_id, device_id,
    user_txn_count_5m, user_txn_count_10m, user_txn_count_1h,
    user_txn_amount_sum_5m, user_txn_amount_sum_10m, user_txn_amount_sum_1h,
    user_distinct_merchants_5m, user_distinct_merchants_10m, user_distinct_merchants_1h,
    user_failed_logins_15m, user_failed_logins_1h,
    device_txn_count_5m, device_txn_count_10m, device_txn_count_1h
) VALUES (
    %(transaction_id)s, %(user_id)s, %(device_id)s,
    %(user_txn_count_5m)s, %(user_txn_count_10m)s, %(user_txn_count_1h)s,
    %(user_txn_amount_sum_5m)s, %(user_txn_amount_sum_10m)s, %(user_txn_amount_sum_1h)s,
    %(user_distinct_merchants_5m)s, %(user_distinct_merchants_10m)s, %(user_distinct_merchants_1h)s,
    %(user_failed_logins_15m)s, %(user_failed_logins_1h)s,
    %(device_txn_count_5m)s, %(device_txn_count_10m)s, %(device_txn_count_1h)s
)
ON CONFLICT DO NOTHING
"""


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            user=os.getenv("POSTGRES_USER", "fraud_user"),
            password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
            dbname=os.getenv("POSTGRES_DB", "fraud_db"),
        )
    return _conn


def log_online_features(
    transaction_id: str,
    user_id: str,
    device_id: str,
    online_features: dict,
) -> None:
    """
    Persist the online feature snapshot for one inference call.

    Parameters
    ----------
    transaction_id  : The transaction being scored.
    user_id         : Entity ID for user.
    device_id       : Entity ID for device.
    online_features : The dict returned by retriever.get_all_online_features().
                      Missing keys default to 0.
    """
    params = {
        "transaction_id": transaction_id,
        "user_id":        user_id,
        "device_id":      device_id,
    }
    for col in ONLINE_FEATURE_COLS:
        val = online_features.get(col, 0)
        params[col] = val if val is not None else 0

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(_INSERT, params)
        conn.commit()
    except Exception as exc:
        logger.warning("feature_logger: failed to write online_feature_log — %s", exc)
        try:
            _conn.rollback()
        except Exception:
            pass
