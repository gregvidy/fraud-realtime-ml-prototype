"""
score_logger.py
---------------
Persists inference results to model_score_log for monitoring.
Fire-and-forget: logs a warning on failure but never raises so a DB
hiccup never breaks the scoring response.
"""

import logging
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_conn = None


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


_INSERT = """
INSERT INTO model_score_log
    (transaction_id, user_id, device_id, merchant_id,
     fraud_score, risk_band, is_flagged, model_version,
     feature_service_version, feast_offline_ok, redis_online_ok)
VALUES
    (%(transaction_id)s, %(user_id)s, %(device_id)s, %(merchant_id)s,
     %(fraud_score)s, %(risk_band)s, %(is_flagged)s, %(model_version)s,
     %(feature_service_version)s, %(feast_offline_ok)s, %(redis_online_ok)s)
ON CONFLICT DO NOTHING
"""


def log_score(
    transaction_id: str,
    user_id: str,
    device_id: str,
    merchant_id: str,
    fraud_score: float,
    risk_band: str,
    is_flagged: bool,
    model_version: str,
    feature_service_version: str,
    feast_offline_ok: bool,
    redis_online_ok: bool,
) -> None:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(_INSERT, {
                "transaction_id":          transaction_id,
                "user_id":                 user_id,
                "device_id":               device_id,
                "merchant_id":             merchant_id,
                "fraud_score":             fraud_score,
                "risk_band":               risk_band,
                "is_flagged":              is_flagged,
                "model_version":           model_version,
                "feature_service_version": feature_service_version,
                "feast_offline_ok":        feast_offline_ok,
                "redis_online_ok":         redis_online_ok,
            })
        conn.commit()
    except Exception as exc:
        logger.warning("score_logger: failed to write score log — %s", exc)
        try:
            _conn.rollback()
        except Exception:
            pass
