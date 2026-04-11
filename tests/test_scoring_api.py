"""
tests/test_scoring_api.py
--------------------------
Tests for the FastAPI scoring endpoint using TestClient.
No live infrastructure required — model and Redis are mocked.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Minimal mock model + meta setup
# ---------------------------------------------------------------------------

MOCK_SCORE  = 0.72
MOCK_META   = {
    "model_name":   "fraud_xgb_v1",
    "threshold":    0.5,
    "feature_cols": ["txn_amount", "is_international", "local_hour"],
}

SAMPLE_REQUEST = {
    "transaction_id": "test-txn-001",
    "user_id":        "u_000001",
    "device_id":      "d_0000001",
    "merchant_id":    "m_00001",
    "amount":         350.00,
    "currency":       "USD",
    "payment_method": "card",
    "country_code":   "US",
    "is_international": False,
    "local_hour":     14,
}


@pytest.fixture(autouse=True)
def mock_model_and_features():
    """Patch model loader and feature retrieval for all tests in this module."""
    mock_model = MagicMock()
    import numpy as np
    mock_model.predict_proba.return_value = np.array([[1 - MOCK_SCORE, MOCK_SCORE]])

    with (
        patch("app.model_loader._model",  mock_model),
        patch("app.model_loader._meta",   MOCK_META),
        patch("app.feature_fetcher.fetch_offline_features", return_value=({}, False)),
        patch("app.feature_fetcher.fetch_online_features",  return_value=({}, False)),
    ):
        yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScoreEndpoint:
    def test_score_returns_200(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        assert response.status_code == 200

    def test_score_response_has_required_fields(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        body = response.json()
        assert "transaction_id" in body
        assert "score"          in body
        assert "risk_band"      in body
        assert "is_flagged"     in body
        assert "model_version"  in body

    def test_score_transaction_id_matches(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        assert response.json()["transaction_id"] == SAMPLE_REQUEST["transaction_id"]

    def test_score_value_in_range(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        score = response.json()["score"]
        assert 0.0 <= score <= 1.0

    def test_high_score_is_flagged(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        body = response.json()
        # MOCK_SCORE=0.72 > threshold=0.5
        assert body["is_flagged"] is True

    def test_risk_band_is_valid(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        assert response.json()["risk_band"] in {"low", "medium", "high", "critical"}

    def test_missing_required_field_returns_422(self, client):
        bad_request = {k: v for k, v in SAMPLE_REQUEST.items() if k != "user_id"}
        response = client.post("/score", json=bad_request)
        assert response.status_code == 422

    def test_negative_amount_returns_422(self, client):
        bad_request = {**SAMPLE_REQUEST, "amount": -10.0}
        response = client.post("/score", json=bad_request)
        assert response.status_code == 422

    def test_feature_sources_in_response(self, client):
        response = client.post("/score", json=SAMPLE_REQUEST)
        sources = response.json().get("feature_sources", {})
        assert "request_time" in sources
        assert sources["request_time"] is True


class TestHealthEndpoint:
    @patch("app.main.redis_lib.Redis")
    def test_health_returns_200(self, mock_redis_cls, client):
        mock_redis_cls.return_value.ping.return_value = True
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_has_status_field(self, client):
        response = client.get("/health")
        assert "status" in response.json()
