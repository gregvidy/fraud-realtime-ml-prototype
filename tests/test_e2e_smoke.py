"""
tests/test_e2e_smoke.py
------------------------
End-to-end smoke test: verifies the scoring API returns a valid response
using the live stack (Postgres + Redis + loaded model).

Skip conditions:
  - SKIP_E2E=1 env var is set
  - Required services are not reachable

Run with:
    pytest tests/test_e2e_smoke.py -v
"""

import os
import pytest

SKIP_E2E = os.getenv("SKIP_E2E", "0") == "1"


def _check_api_reachable(base_url: str) -> bool:
    try:
        import httpx
        r = httpx.get(f"{base_url}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(SKIP_E2E, reason="E2E tests disabled (SKIP_E2E=1)")
class TestE2ESmoke:
    BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

    @pytest.fixture(autouse=True)
    def requires_api(self):
        if not _check_api_reachable(self.BASE_URL):
            pytest.skip("API not reachable — start with 'make start-api'")

    def test_health_ok(self):
        import httpx
        r = httpx.get(f"{self.BASE_URL}/health", timeout=5.0)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in {"ok", "degraded"}
        assert body["model_loaded"] is True

    def test_score_e2e(self):
        import httpx
        payload = {
            "transaction_id": "smoke-test-001",
            "user_id":        "u_000001",
            "device_id":      "d_0000001",
            "merchant_id":    "m_00001",
            "amount":         250.00,
            "currency":       "USD",
            "payment_method": "card",
            "country_code":   "US",
            "is_international": False,
        }
        r = httpx.post(f"{self.BASE_URL}/score", json=payload, timeout=10.0)
        assert r.status_code == 200
        body = r.json()
        assert 0.0 <= body["score"] <= 1.0
        assert body["risk_band"] in {"low", "medium", "high", "critical"}

    def test_score_invalid_amount_rejected(self):
        import httpx
        payload = {
            "transaction_id": "smoke-bad-001",
            "user_id":        "u_000001",
            "device_id":      "d_0000001",
            "merchant_id":    "m_00001",
            "amount":         -1.0,
        }
        r = httpx.post(f"{self.BASE_URL}/score", json=payload, timeout=5.0)
        assert r.status_code == 422
