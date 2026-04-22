"""
tests/test_feast_direct.py
---------------------------
Unit tests for app/feast_direct.py — the Feast SDK bypass layer.

Uses a mock async Redis client to avoid requiring a live Redis instance.
"""

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import mmh3
import pytest

import app.feast_direct as fd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mmh3_field(name: str) -> bytes:
    h = mmh3.hash(name, signed=False)
    return bytes.fromhex(struct.pack("<Q", h).hex()[:8])


def _pack_int64(value: int) -> bytes:
    """Encode int64_val as protobuf field 4 varint (tag=0x20)."""
    out = bytearray([0x20])
    v = value
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pack_double(value: float) -> bytes:
    """Encode double_val as protobuf field 5 fixed64 (tag=0x29)."""
    return b"\x29" + struct.pack("<d", value)


def _make_pipeline_mock(u_vals, d_vals, m_vals):
    mock_pipe = AsyncMock()
    mock_pipe.hmget = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[u_vals, d_vals, m_vals])
    mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_pipe.__aexit__ = AsyncMock(return_value=None)
    mock_redis = MagicMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)
    return mock_redis, mock_pipe


# ---------------------------------------------------------------------------
# Tests: _build_entity_key
# ---------------------------------------------------------------------------

class TestBuildEntityKey:
    def test_user_key_matches_feast_format(self):
        key = fd._build_entity_key("user_id", "u_001259")
        # Expected: STRING_TAG + "user_id" + STRING_TAG + len("u_001259") + "u_001259" + project
        expected = (
            b"\x02\x00\x00\x00"
            + b"user_id"
            + b"\x02\x00\x00\x00"
            + struct.pack("<I", 8)
            + b"u_001259"
            + b"fraud_feature_store"
        )
        assert key == expected

    def test_device_key_structure(self):
        key = fd._build_entity_key("device_id", "d_0000001")
        assert b"device_id" in key
        assert b"d_0000001" in key
        assert b"fraud_feature_store" in key

    def test_merchant_key_structure(self):
        key = fd._build_entity_key("merchant_id", "m_00001")
        assert b"merchant_id" in key
        assert b"m_00001" in key

    def test_project_name_appended_at_end(self):
        key = fd._build_entity_key("user_id", "u_001")
        assert key.endswith(b"fraud_feature_store")


# ---------------------------------------------------------------------------
# Tests: _decode_value
# ---------------------------------------------------------------------------

class TestDecodeValue:
    def test_decode_int64_small(self):
        assert fd._decode_value(_pack_int64(1)) == 1

    def test_decode_int64_zero(self):
        assert fd._decode_value(_pack_int64(0)) == 0

    def test_decode_int64_large(self):
        assert fd._decode_value(_pack_int64(1205)) == 1205

    def test_decode_double(self):
        val = fd._decode_value(_pack_double(3.14))
        assert abs(val - 3.14) < 1e-10

    def test_decode_double_zero(self):
        assert fd._decode_value(_pack_double(0.0)) == 0.0

    def test_decode_none_returns_zero(self):
        assert fd._decode_value(None) == 0

    def test_decode_empty_returns_zero(self):
        assert fd._decode_value(b"") == 0

    def test_decode_unknown_tag_returns_zero(self):
        assert fd._decode_value(b"\xFF\x01\x02") == 0


# ---------------------------------------------------------------------------
# Tests: field hash pre-computation
# ---------------------------------------------------------------------------

class TestFieldHashes:
    def test_user_account_age_days_hash(self):
        expected = _mmh3_field("user_batch_fv_v1:user_account_age_days")
        actual = fd._USER_FIELDS[0][0]
        assert actual == expected, f"got {actual.hex()}, expected {expected.hex()}"

    def test_device_distinct_users_30d_hash(self):
        expected = _mmh3_field("device_batch_fv_v1:device_distinct_users_30d")
        actual = fd._DEVICE_FIELDS[0][0]
        assert actual == expected

    def test_merchant_is_high_risk_hash(self):
        expected = _mmh3_field("merchant_batch_fv_v1:merchant_is_high_risk")
        actual = fd._MERCHANT_FIELDS[0][0]
        assert actual == expected

    def test_user_field_count(self):
        assert len(fd._USER_FIELDS) == 26

    def test_device_field_count(self):
        assert len(fd._DEVICE_FIELDS) == 7

    def test_merchant_field_count(self):
        assert len(fd._MERCHANT_FIELDS) == 5

    def test_all_user_field_names_present(self):
        names = {fname for _, fname in fd._USER_FIELDS}
        assert "user_account_age_days" in names
        assert "user_failed_logins_15m" in names
        assert "user_txn_amount_sum_1h" in names

    def test_no_duplicate_field_hashes(self):
        all_hashes = (
            [fh for fh, _ in fd._USER_FIELDS]
            + [fh for fh, _ in fd._DEVICE_FIELDS]
            + [fh for fh, _ in fd._MERCHANT_FIELDS]
        )
        assert len(all_hashes) == len(set(all_hashes)), "Duplicate field hashes detected"


# ---------------------------------------------------------------------------
# Tests: fetch_offline_features_direct (async, mocked Redis)
# ---------------------------------------------------------------------------

class TestFetchOfflineFeaturesDirect:
    @patch("app.feast_direct._get_redis")
    async def test_returns_all_feature_names(self, mock_get_redis):
        n_user = len(fd._USER_FIELDS)
        n_dev  = len(fd._DEVICE_FIELDS)
        n_merch = len(fd._MERCHANT_FIELDS)

        u_vals = [_pack_int64(i + 1) for i in range(n_user)]
        d_vals = [_pack_int64(i + 1) for i in range(n_dev)]
        m_vals = [_pack_int64(i + 1) for i in range(n_merch)]

        mock_redis, _ = _make_pipeline_mock(u_vals, d_vals, m_vals)
        mock_get_redis.return_value = mock_redis

        feats, ok = await fd.fetch_offline_features_direct("u_001", "d_001", "m_001")

        assert ok is True
        assert "user_account_age_days"   in feats
        assert "device_distinct_users_30d" in feats
        assert "merchant_is_high_risk"   in feats

    @patch("app.feast_direct._get_redis")
    async def test_int64_values_decoded_correctly(self, mock_get_redis):
        u_vals  = [_pack_int64(100)] + [_pack_int64(0)] * (len(fd._USER_FIELDS) - 1)
        d_vals  = [_pack_int64(0)] * len(fd._DEVICE_FIELDS)
        m_vals  = [_pack_int64(0)] * len(fd._MERCHANT_FIELDS)

        mock_redis, _ = _make_pipeline_mock(u_vals, d_vals, m_vals)
        mock_get_redis.return_value = mock_redis

        feats, ok = await fd.fetch_offline_features_direct("u_001", "d_001", "m_001")

        assert feats["user_account_age_days"] == 100
        assert ok is True

    @patch("app.feast_direct._get_redis")
    async def test_double_values_decoded_correctly(self, mock_get_redis):
        # merchant_avg_ticket_30d is the 4th merchant field (index 3)
        m_vals = [_pack_int64(0)] * len(fd._MERCHANT_FIELDS)
        m_vals[3] = _pack_double(250.75)

        u_vals = [_pack_int64(0)] * len(fd._USER_FIELDS)
        d_vals = [_pack_int64(0)] * len(fd._DEVICE_FIELDS)

        mock_redis, _ = _make_pipeline_mock(u_vals, d_vals, m_vals)
        mock_get_redis.return_value = mock_redis

        feats, ok = await fd.fetch_offline_features_direct("u_001", "d_001", "m_001")

        assert abs(feats["merchant_avg_ticket_30d"] - 250.75) < 1e-6

    @patch("app.feast_direct._get_redis")
    async def test_none_values_default_to_zero(self, mock_get_redis):
        mock_redis, _ = _make_pipeline_mock(
            [None] * len(fd._USER_FIELDS),
            [None] * len(fd._DEVICE_FIELDS),
            [None] * len(fd._MERCHANT_FIELDS),
        )
        mock_get_redis.return_value = mock_redis

        feats, ok = await fd.fetch_offline_features_direct("new_user", "new_dev", "new_merch")

        assert ok is False  # all None → entity not found
        for v in feats.values():
            assert v == 0

    @patch("app.feast_direct._get_redis")
    async def test_pipeline_called_once(self, mock_get_redis):
        mock_redis, mock_pipe = _make_pipeline_mock(
            [_pack_int64(1)] * len(fd._USER_FIELDS),
            [_pack_int64(1)] * len(fd._DEVICE_FIELDS),
            [_pack_int64(1)] * len(fd._MERCHANT_FIELDS),
        )
        mock_get_redis.return_value = mock_redis

        await fd.fetch_offline_features_direct("u_001", "d_001", "m_001")

        mock_redis.pipeline.assert_called_once()
        assert mock_pipe.hmget.call_count == 3  # one per entity type

    @patch("app.feast_direct._get_redis")
    async def test_redis_error_returns_empty_not_ok(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_pipe  = AsyncMock()
        mock_pipe.hmget = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)
        mock_get_redis.return_value = mock_redis

        feats, ok = await fd.fetch_offline_features_direct("u_001", "d_001", "m_001")

        assert ok is False
        assert feats == {}
