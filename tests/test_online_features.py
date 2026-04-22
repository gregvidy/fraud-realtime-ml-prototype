"""
tests/test_online_features.py
------------------------------
Unit tests for Redis online feature updater and retriever.
Uses a mock Redis client to avoid requiring a live Redis instance.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

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
# retriever — async pipelined version
# ---------------------------------------------------------------------------

class TestRetriever:
    def _make_pipeline_mock(self, results: list) -> MagicMock:
        """
        Return a mock async Redis client whose pipeline returns the given
        results list from pipe.execute().

        results must have 11 entries — one per ZRANGEBYSCORE in the pipeline.
        """
        mock_pipe = AsyncMock()
        mock_pipe.zrangebyscore = MagicMock()   # sync queue call, returns pipe
        mock_pipe.execute = AsyncMock(return_value=results)
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)
        return mock_redis

    @patch("app.online_features.retriever._redis_client")
    async def test_get_all_online_features_returns_expected_keys(self, mock_redis):
        from app.online_features.retriever import get_all_online_features

        members = [encode_txn_member(f"t{i}", float(i * 10)) for i in range(5)]
        # 11 pipeline results: txn_5m, txn_10m, txn_1h, merch_5m, merch_10m,
        # merch_1h, dev_5m, dev_10m, dev_1h, login_15m, login_1h
        mock_pipe = AsyncMock()
        mock_pipe.zrangebyscore = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[
            members, members, members,  # user txn windows
            members, members, members,  # user merchant windows
            members, members, members,  # device txn windows
            [], [],                     # login windows
        ])
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        result = await get_all_online_features("u_001", "d_001")

        assert "user_txn_count_5m"           in result
        assert "user_txn_count_10m"          in result
        assert "user_txn_count_1h"           in result
        assert "user_txn_amount_sum_5m"      in result
        assert "user_distinct_merchants_5m"  in result
        assert "user_failed_logins_15m"      in result
        assert "device_txn_count_5m"         in result
        assert result["user_txn_count_5m"]   == len(members)
        assert isinstance(result["user_txn_count_10m"], int)

    @patch("app.online_features.retriever._redis_client")
    async def test_zero_features_for_new_entity(self, mock_redis):
        from app.online_features.retriever import get_all_online_features

        mock_pipe = AsyncMock()
        mock_pipe.zrangebyscore = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[[], [], [], [], [], [], [], [], [], [], []])
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        result = await get_all_online_features("new_user_never_seen", "new_device")

        assert result["user_txn_count_5m"]   == 0
        assert result["user_txn_count_10m"]  == 0
        assert result["user_txn_count_1h"]   == 0
        assert result["device_txn_count_5m"] == 0
        assert result["user_failed_logins_15m"] == 0

    @patch("app.online_features.retriever._redis_client")
    async def test_amount_sum_computed_from_pipeline_results(self, mock_redis):
        from app.online_features.retriever import get_all_online_features

        # 3 members each with amounts 10, 20, 30 → sum = 60
        members = [encode_txn_member(f"t{i}", float((i + 1) * 10)) for i in range(3)]
        empty   = []

        mock_pipe = AsyncMock()
        mock_pipe.zrangebyscore = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[
            members, empty, empty,   # user_txn: 5m has members, 10m/1h empty
            empty, empty, empty,     # user_merchant
            empty, empty, empty,     # device_txn
            empty, empty,            # login_fail
        ])
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        result = await get_all_online_features("u_001", "d_001")

        assert result["user_txn_count_5m"]      == 3
        assert result["user_txn_amount_sum_5m"]  == 60.0
        assert result["user_txn_count_10m"]      == 0
        assert result["user_txn_amount_sum_10m"] == 0.0

    @patch("app.online_features.retriever._redis_client")
    async def test_pipeline_called_once_per_request(self, mock_redis):
        """All features must be fetched in a single pipeline() call."""
        from app.online_features.retriever import get_all_online_features

        mock_pipe = AsyncMock()
        mock_pipe.zrangebyscore = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[[], [], [], [], [], [], [], [], [], [], []])
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        await get_all_online_features("u_001", "d_001")

        mock_redis.pipeline.assert_called_once()
        assert mock_pipe.zrangebyscore.call_count == 11  # exactly 11 queued commands
