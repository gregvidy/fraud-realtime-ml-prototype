"""
tests/test_online_features.py
------------------------------
Unit tests for Redis online feature updater and retriever.
Uses a mock Redis client to avoid requiring a live Redis instance.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.online_features.redis_keys import (
    WINDOW_10M,
    WINDOW_5M,
    decode_txn_member,
    encode_txn_member,
    user_txn_zset,
    device_txn_zset,
)


# ---------------------------------------------------------------------------
# redis_keys helpers
# ---------------------------------------------------------------------------

class TestRedisKeys:
    def test_user_txn_zset_format(self):
        key = user_txn_zset("u_000001")
        assert key == "fraud:user:u_000001:txn_ts"

    def test_device_txn_zset_format(self):
        key = device_txn_zset("d_0000001")
        assert key == "fraud:device:d_0000001:txn_ts"

    def test_encode_decode_roundtrip(self):
        txn_id = "abc-123"
        amount = 250.1234
        member = encode_txn_member(txn_id, amount)
        decoded_id, decoded_amount = decode_txn_member(member)
        assert decoded_id == txn_id
        assert abs(decoded_amount - amount) < 0.0001

    def test_encode_txn_member_contains_colon(self):
        member = encode_txn_member("txn-xyz", 100.0)
        assert ":" in member


# ---------------------------------------------------------------------------
# updater — uses mocked Redis
# ---------------------------------------------------------------------------

class TestUpdater:
    @patch("app.online_features.updater._redis_client")
    def test_update_online_features_calls_pipeline(self, mock_redis):
        from app.online_features.updater import update_online_features

        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        event = {
            "transaction_id": "txn-001",
            "user_id": "u_000001",
            "device_id": "d_0000001",
            "merchant_id": "m_00001",
            "amount": 150.0,
            "txn_status": "success",
            "event_timestamp": "2026-04-11T10:00:00+00:00",
        }
        update_online_features(event)

        assert mock_pipe.zadd.called
        assert mock_pipe.execute.called

    @patch("app.online_features.updater._redis_client")
    def test_update_sets_user_and_device_keys(self, mock_redis):
        from app.online_features.updater import update_online_features

        zadd_keys = []
        mock_pipe = MagicMock()
        mock_pipe.zadd.side_effect = lambda key, mapping: zadd_keys.append(key)
        mock_redis.pipeline.return_value = mock_pipe

        event = {
            "transaction_id": "txn-002",
            "user_id": "u_999",
            "device_id": "d_888",
            "merchant_id": "m_777",
            "amount": 99.0,
            "event_timestamp": "2026-04-11T10:00:00+00:00",
        }
        update_online_features(event)

        assert any("u_999" in k for k in zadd_keys), f"User key not set: {zadd_keys}"
        assert any("d_888" in k for k in zadd_keys), f"Device key not set: {zadd_keys}"


# ---------------------------------------------------------------------------
# retriever — uses mocked Redis
# ---------------------------------------------------------------------------

class TestRetriever:
    def _make_mock_redis(self, members_5m, members_10m, members_1h):
        """Return a mock Redis that returns different member lists by window."""
        mock_r = MagicMock()

        def zrangebyscore_side_effect(key, min_score, max_score):
            now = time.time()
            window = now - float(min_score) if float(min_score) != float("-inf") else None
            if window is not None:
                if abs(window - WINDOW_5M) < 5:
                    return members_5m
                elif abs(window - WINDOW_10M) < 5:
                    return members_10m
                else:
                    return members_1h
            return []

        mock_r.zrangebyscore.side_effect = zrangebyscore_side_effect
        return mock_r

    @patch("app.online_features.retriever._redis_client")
    def test_get_user_online_features_returns_counts(self, mock_redis):
        from app.online_features.retriever import get_user_online_features

        members_5m  = [encode_txn_member(f"t{i}", float(i * 10)) for i in range(3)]
        members_10m = [encode_txn_member(f"t{i}", float(i * 10)) for i in range(7)]
        members_1h  = [encode_txn_member(f"t{i}", float(i * 10)) for i in range(15)]

        mock_redis.zrangebyscore.return_value = members_10m

        result = get_user_online_features("u_001")

        assert "user_txn_count_5m"       in result
        assert "user_txn_count_10m"      in result
        assert "user_txn_count_1h"       in result
        assert "user_txn_amount_sum_10m" in result
        assert isinstance(result["user_txn_count_10m"], int)

    @patch("app.online_features.retriever._redis_client")
    def test_get_device_online_features_returns_counts(self, mock_redis):
        from app.online_features.retriever import get_device_online_features

        mock_redis.zrangebyscore.return_value = []

        result = get_device_online_features("d_001")

        assert "device_txn_count_5m"  in result
        assert "device_txn_count_10m" in result

    @patch("app.online_features.retriever._redis_client")
    def test_zero_features_for_new_entity(self, mock_redis):
        from app.online_features.retriever import get_user_online_features

        mock_redis.zrangebyscore.return_value = []

        result = get_user_online_features("new_user_never_seen")

        assert result["user_txn_count_5m"]  == 0
        assert result["user_txn_count_10m"] == 0
        assert result["user_txn_count_1h"]  == 0
