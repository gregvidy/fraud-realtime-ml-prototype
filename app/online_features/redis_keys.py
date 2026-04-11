"""
redis_keys.py
-------------
Central definition of Redis key patterns and TTL values for online features.

Key strategy:
  - Sorted sets store (score=event_timestamp_unix, member=transaction_id:amount)
    to support sliding-window COUNT and SUM without full key scans.
  - All keys are namespaced: fraud:<entity_type>:<entity_id>:<feature>

TTL is set conservatively longer than the maximum window so stale data
is cleaned up automatically.
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# TTL constants (seconds)
# ---------------------------------------------------------------------------
TTL_5M   =   5 * 60
TTL_10M  =  10 * 60
TTL_1H   =  60 * 60
TTL_24H  =  24 * 60 * 60
TTL_ZSET =  TTL_24H  # sorted set TTL — covers all windows


# ---------------------------------------------------------------------------
# Window sizes (seconds) — used by updater/retriever to ZRANGEBYSCORE
# ---------------------------------------------------------------------------
WINDOW_5M  =   5 * 60
WINDOW_10M =  10 * 60
WINDOW_1H  =  60 * 60


# ---------------------------------------------------------------------------
# Key templates
# ---------------------------------------------------------------------------
def user_txn_zset(user_id: str) -> str:
    """Sorted set: {score=ts_unix, member=txn_id:amount} for user transactions."""
    return f"fraud:user:{user_id}:txn_ts"


def user_merchant_zset(user_id: str) -> str:
    """Sorted set: {score=ts_unix, member=merchant_id} for distinct merchant tracking."""
    return f"fraud:user:{user_id}:merchant_ts"


def device_txn_zset(device_id: str) -> str:
    """Sorted set: {score=ts_unix, member=txn_id:amount} for device transactions."""
    return f"fraud:device:{device_id}:txn_ts"


def user_login_fail_zset(user_id: str) -> str:
    """Sorted set: {score=ts_unix, member=login_event_id} for failed logins."""
    return f"fraud:user:{user_id}:login_fail_ts"


# ---------------------------------------------------------------------------
# Member encoding helpers
# ---------------------------------------------------------------------------
def encode_txn_member(txn_id: str, amount: float) -> str:
    return f"{txn_id}:{amount:.4f}"


def decode_txn_member(member: str) -> tuple[str, float]:
    parts = member.rsplit(":", 1)
    return parts[0], float(parts[1])
